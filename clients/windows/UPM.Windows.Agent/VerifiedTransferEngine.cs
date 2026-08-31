using System.Security.Cryptography;

namespace UPM.Windows.Agent;

public sealed record DownloadRequest(Guid VersionId, string OriginalFilename, string ExpectedSha256, long ExpectedSize);

public sealed class VerifiedTransferEngine(AgentStorage storage)
{
  private static readonly HashSet<string> PresentationExtensions = new(StringComparer.OrdinalIgnoreCase) { ".ppt", ".pptx", ".pps", ".ppsx" };

  public async Task<string> DownloadAsync(DownloadRequest request, Stream source, CancellationToken ct = default)
  {
    if (!PresentationExtensions.Contains(Path.GetExtension(request.OriginalFilename)))
      throw new InvalidDataException("The asset is not a supported presentation type.");
    storage.EnsureCreated();
    var versionDirectory = WindowsPathPolicy.EnsureContained(storage.Cache, "presentations", request.VersionId.ToString("N"));
    Directory.CreateDirectory(versionDirectory);
    var filename = WindowsPathPolicy.UploadedFilename(request.OriginalFilename);
    var final = WindowsPathPolicy.EnsureContained(versionDirectory, filename);
    var partial = WindowsPathPolicy.EnsureContained(storage.Downloads, $"{request.VersionId:N}.partial");
    try
    {
      await using (var output = new FileStream(partial, FileMode.Create, FileAccess.Write, FileShare.None, 1024 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan))
        await source.CopyToAsync(output, ct);
      var info = new FileInfo(partial);
      if (info.Length != request.ExpectedSize) throw new InvalidDataException($"Expected {request.ExpectedSize} bytes but received {info.Length}.");
      await using var verify = new FileStream(partial, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, FileOptions.Asynchronous | FileOptions.SequentialScan);
      var actual = Convert.ToHexString(await SHA256.HashDataAsync(verify, ct)).ToLowerInvariant();
      if (!CryptographicOperations.FixedTimeEquals(Convert.FromHexString(actual), Convert.FromHexString(request.ExpectedSha256)))
        throw new InvalidDataException("Downloaded presentation SHA-256 did not match Site metadata.");
      File.Move(partial, final, true);
      return final;
    }
    catch
    {
      if (File.Exists(partial)) File.Delete(partial);
      throw;
    }
  }
}
