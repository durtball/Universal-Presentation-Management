using System.Security.Cryptography;

namespace UPM.Windows.Core;

public sealed record PresentationCacheRequest(
    Guid PresentationVersionId,
    string OriginalFilename,
    long? ExpectedSize,
    string? ExpectedSha256);

public sealed record PresentationCacheResult(string Path, bool CacheHit, long Size, string Sha256);

/// <summary>Verified, immutable-version keyed cache for Site-to-workstation presentation downloads.</summary>
public sealed class PresentationCache(string rootPath)
{
  public async Task<PresentationCacheResult> GetOrDownloadAsync(
      PresentationCacheRequest request,
      Func<Stream, IProgress<double>?, CancellationToken, Task> download,
      IProgress<double>? progress,
      CancellationToken cancellationToken)
  {
    Directory.CreateDirectory(rootPath);
    var safeName = SafeFilename(request.OriginalFilename);
    var directory = Path.Combine(rootPath, request.PresentationVersionId.ToString("N"));
    var finalPath = Path.Combine(directory, safeName);
    Directory.CreateDirectory(directory);

    if (File.Exists(finalPath))
    {
      var verified = await VerifyAsync(finalPath, request, cancellationToken);
      if (verified is not null) return verified with { CacheHit = true };
      File.Delete(finalPath);
    }

    var partialPath = finalPath + ".partial";
    try
    {
      await using (var target = new FileStream(
          partialPath, FileMode.Create, FileAccess.Write, FileShare.None, 1024 * 128,
          FileOptions.Asynchronous | FileOptions.SequentialScan))
      {
        await download(target, progress, cancellationToken);
        await target.FlushAsync(cancellationToken);
      }
      var result = await VerifyAsync(partialPath, request, cancellationToken)
          ?? throw new InvalidDataException("Downloaded presentation failed size or SHA-256 verification.");
      File.Move(partialPath, finalPath, true);
      return result with { Path = finalPath };
    }
    catch
    {
      if (File.Exists(partialPath)) File.Delete(partialPath);
      throw;
    }
  }

  private static async Task<PresentationCacheResult?> VerifyAsync(
      string path, PresentationCacheRequest request, CancellationToken cancellationToken)
  {
    var info = new FileInfo(path);
    if (request.ExpectedSize.HasValue && info.Length != request.ExpectedSize.Value) return null;
    await using var source = new FileStream(
        path, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 128,
        FileOptions.Asynchronous | FileOptions.SequentialScan);
    var hash = Convert.ToHexString(await SHA256.HashDataAsync(source, cancellationToken)).ToLowerInvariant();
    if (!string.IsNullOrWhiteSpace(request.ExpectedSha256) &&
        !hash.Equals(request.ExpectedSha256, StringComparison.OrdinalIgnoreCase)) return null;
    return new PresentationCacheResult(path, false, info.Length, hash);
  }

  private static string SafeFilename(string filename)
  {
    var supplied = Path.GetFileName(filename);
    var safe = string.Concat(supplied.Select(character =>
        Path.GetInvalidFileNameChars().Contains(character) ? '_' : character));
    return string.IsNullOrWhiteSpace(safe) ? "presentation.bin" : safe;
  }
}
