using System.Security.Cryptography;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;

namespace UPM.Windows.Transfers;

public static class IntakeEnumerator
{
  private static readonly HashSet<string> Supported = new(StringComparer.OrdinalIgnoreCase)
    {
        ".pptx", ".pptm", ".ppsx", ".pdf", ".mp4", ".mov", ".png", ".jpg", ".jpeg",
    };

  public static async IAsyncEnumerable<TransferItem> EnumerateAsync(
      IEnumerable<string> roots,
      Guid profileId,
      Guid? eventId,
      [System.Runtime.CompilerServices.EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    foreach (var root in roots)
    {
      var basePath = Directory.Exists(root) ? root : Path.GetDirectoryName(root)!;
      var files = Directory.Exists(root)
          ? Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
          : [root];
      foreach (var file in files)
      {
        cancellationToken.ThrowIfCancellationRequested();
        if (!Supported.Contains(Path.GetExtension(file)))
        {
          continue;
        }

        var info = new FileInfo(file);
        var initialLength = info.Length;
        var initialWrite = info.LastWriteTimeUtc;
        await Task.Delay(TimeSpan.FromMilliseconds(250), cancellationToken);
        info.Refresh();
        if (!info.Exists || info.Length != initialLength || info.LastWriteTimeUtc != initialWrite)
          throw new IOException($"File is still being written and was not queued: {file}");
        yield return new TransferItem(
            Guid.CreateVersion7(),
            profileId,
            eventId,
            file,
            info.Name,
            Path.GetRelativePath(basePath, file),
            Path.GetPathRoot(file),
            initialLength,
            initialWrite,
            TransferIdentity.Create(profileId, eventId, file, initialLength, initialWrite));
        await Task.Yield();
      }
    }
  }
}

public interface IRemovableDriveScanner
{
  IAsyncEnumerable<DriveInfo> WatchAsync(CancellationToken cancellationToken);
  IAsyncEnumerable<TransferItem> ScanAsync(
      DriveInfo drive,
      Guid profileId,
      Guid? eventId,
      CancellationToken cancellationToken);
}

public sealed class TransferWorker(
    LocalStateStore store,
    ISiteTransferRouter router,
    ILogger<TransferWorker> logger) : BackgroundService
{
  private const int MaxRetries = 5;
  private readonly SemaphoreSlim signal = new(0, 1);

  public void Signal() { if (signal.CurrentCount == 0) signal.Release(); }

  public static Task<SiteTransferDestination> ResolveDestinationAsync(
      TransferItem item,
      ISiteTransferRouter router,
      CancellationToken cancellationToken) =>
      router.ResolveAsync(item.SiteProfileId, cancellationToken);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      var item = await store.ClaimDueAsync(stoppingToken);
      if (item is not null)
      {
        await ProcessAsync(item, stoppingToken);
        continue;
      }
      using var poll = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
      poll.CancelAfter(TimeSpan.FromSeconds(2));
      try { await signal.WaitAsync(poll.Token); }
      catch (OperationCanceledException) when (!stoppingToken.IsCancellationRequested) { }
    }
  }

  private async Task ProcessAsync(TransferItem item, CancellationToken cancellationToken)
  {
      try
      {
        string hash;
        await using (var file = new FileStream(
            item.SourcePath,
            FileMode.Open,
            FileAccess.Read,
            FileShare.Read,
            1024 * 1024,
            FileOptions.Asynchronous | FileOptions.SequentialScan))
        {
          hash = Convert.ToHexString(await SHA256.HashDataAsync(file, cancellationToken)).ToLowerInvariant();
          file.Position = 0;
          var destination = await ResolveDestinationAsync(item, router, cancellationToken);
          var existing = await destination.Api.FindIngestionReceiptAsync(
              destination.CanonicalSiteId, item.IdempotencyKey, cancellationToken);
          if (existing is not null)
          {
            ValidateReceipt(item, hash, existing);
            await store.UpdateAsync(item.TransferId, TransferState.ReceivedBySite,
                existing.Size, existing.Sha256, item.RetryCount, receiptId: existing.ReceiptId,
                cancellationToken: cancellationToken);
            return;
          }
          await store.UpdateAsync(
              item.TransferId,
              TransferState.Uploading,
              hash: hash,
              cancellationToken: cancellationToken);
          var receipt = await destination.Api.UploadAsync(
              item,
              destination.CanonicalSiteId,
              file,
              cancellationToken);
          ValidateReceipt(item, hash, receipt);
          await store.UpdateAsync(
              item.TransferId, TransferState.ReceivedBySite, receipt.Size, receipt.Sha256,
              item.RetryCount, receiptId: receipt.ReceiptId, cancellationToken: cancellationToken);
        }
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        return;
      }
      catch (Exception exception)
      {
        var retries = item.RetryCount + 1;
        if (retries >= MaxRetries)
        {
          await store.UpdateAsync(item.TransferId, TransferState.Failed, retry: retries,
              error: $"Transfer stopped after {retries} attempts: {exception.Message}", cancellationToken: cancellationToken);
          logger.LogError(exception, "Transfer {TransferId} exhausted retries; verify Site authentication, free space, and source availability", item.TransferId);
          return;
        }
        var delay = TimeSpan.FromSeconds(
            Math.Min(300, Math.Pow(2, retries)) + Random.Shared.NextDouble());
        await store.UpdateAsync(
            item.TransferId,
            TransferState.RetryWaiting,
            retry: retries,
            retryAt: DateTimeOffset.UtcNow + delay,
            error: exception.Message,
            cancellationToken: cancellationToken);
        logger.LogWarning(
            exception,
            "Transfer {TransferId} for Site profile {ProfileId} will retry",
            item.TransferId,
            item.SiteProfileId);
      }
  }

  private static void ValidateReceipt(TransferItem item, string hash, ByteTransferReceipt receipt)
  {
    if (receipt.Size != item.Length || !receipt.Sha256.Equals(hash, StringComparison.OrdinalIgnoreCase))
      throw new InvalidDataException(
          $"Site receipt integrity mismatch for {item.OriginalFilename}; expected {item.Length} bytes and SHA-256 {hash}.");
  }
}
