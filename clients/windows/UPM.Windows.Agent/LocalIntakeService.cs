using System.Security.Cryptography;
using System.Text;

namespace UPM.Windows.Agent;

public sealed class LocalIntakeService(AgentStorage storage, AgentStateStore state)
{
  private static readonly HashSet<string> Supported = new(StringComparer.OrdinalIgnoreCase) { ".ppt", ".pptx", ".pps", ".ppsx" };

  public async Task<PendingLocalChange> IngestAsync(Guid sessionId, Guid? presentationId, Guid? baseVersionId, string selectedPath, CancellationToken ct = default, string? originalFilename = null)
  {
    var source = Path.GetFullPath(selectedPath); var info = new FileInfo(source);
    if (!info.Exists || !Supported.Contains(info.Extension)) throw new InvalidDataException("Select a supported PowerPoint presentation.");
    storage.EnsureCreated();
    var original = originalFilename ?? info.Name; _ = WindowsPathPolicy.UploadedFilename(original); var localVersion = Guid.CreateVersion7();
    var directory = WindowsPathPolicy.EnsureContained(storage.Pending, localVersion.ToString("N")); Directory.CreateDirectory(directory);
    var managed = WindowsPathPolicy.EnsureContained(directory, WindowsPathPolicy.UploadedFilename(original));
    await using (var input = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true))
    await using (var output = new FileStream(managed, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1024 * 1024, true))
      await input.CopyToAsync(output, ct);
    await using var copied = new FileStream(managed, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true);
    var hash = Convert.ToHexString(await SHA256.HashDataAsync(copied, ct)).ToLowerInvariant();
    var keyInput = $"{sessionId:N}|{presentationId:N}|{baseVersionId:N}|{hash}|{original}";
    var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(keyInput))).ToLowerInvariant();
    var change = new PendingLocalChange(Guid.CreateVersion7(), sessionId, presentationId, localVersion, baseVersionId, key, original, managed, hash, info.Length, LocalChangeState.Pending, DateTimeOffset.UtcNow);
    await state.EnqueueChangeAsync(change, ct);
    await state.UpsertAssetAsync(new(localVersion, AssetKind.Presentation, localVersion, presentationId, sessionId, null, null, null, original, WindowsPathPolicy.UploadedFilename(original), hash, info.Length, managed, true, false, change.CreatedAt), ct);
    return change;
  }
}
