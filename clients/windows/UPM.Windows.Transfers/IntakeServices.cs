using System.Security.Cryptography;
using System.Threading.Channels;
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
        yield return new TransferItem(
            Guid.CreateVersion7(),
            profileId,
            eventId,
            file,
            info.Name,
            Path.GetRelativePath(basePath, file),
            Path.GetPathRoot(file),
            info.Length,
            info.LastWriteTimeUtc,
            TransferIdentity.Create(profileId, eventId, file, info.Length, info.LastWriteTimeUtc));
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
  private readonly Channel<TransferItem> queue = Channel.CreateBounded<TransferItem>(
      new BoundedChannelOptions(256) { FullMode = BoundedChannelFullMode.Wait });

  public ValueTask QueueAsync(TransferItem item, CancellationToken cancellationToken) =>
      queue.Writer.WriteAsync(item, cancellationToken);

  public static Task<SiteTransferDestination> ResolveDestinationAsync(
      TransferItem item,
      ISiteTransferRouter router,
      CancellationToken cancellationToken) =>
      router.ResolveAsync(item.SiteProfileId, cancellationToken);

  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    var workers = Enumerable.Range(0, 4).Select(_ => RunAsync(stoppingToken)).ToArray();
    await foreach (var item in store.LoadPendingAsync(stoppingToken))
    {
      await queue.Writer.WriteAsync(item with { State = TransferState.Queued }, stoppingToken);
    }

    await Task.WhenAll(workers);
  }

  private async Task RunAsync(CancellationToken cancellationToken)
  {
    await foreach (var item in queue.Reader.ReadAllAsync(cancellationToken))
    {
      try
      {
        await store.UpdateAsync(item.TransferId, TransferState.Hashing, cancellationToken: cancellationToken);
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
          await store.UpdateAsync(
              item.TransferId,
              TransferState.Uploading,
              hash: hash,
              cancellationToken: cancellationToken);
          using var response = await destination.Api.UploadAsync(
              item,
              destination.CanonicalSiteId,
              file,
              cancellationToken);
          response.EnsureSuccessStatusCode();
        }

        await store.UpdateAsync(
            item.TransferId,
            TransferState.ReceivedBySite,
            item.Length,
            hash,
            cancellationToken: cancellationToken);
      }
      catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
      {
        break;
      }
      catch (Exception exception)
      {
        var retries = item.RetryCount + 1;
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
        await Task.Delay(delay, cancellationToken);
        await queue.Writer.WriteAsync(
            item with { State = TransferState.RetryWaiting, RetryCount = retries, RetryAt = DateTimeOffset.UtcNow },
            cancellationToken);
      }
    }
  }
}
