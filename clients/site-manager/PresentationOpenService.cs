using UPM.Windows.Core;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager;

public sealed class PresentationOpenService
{
  private readonly PresentationCache cache;
  private readonly HashSet<Guid> activeDownloads = [];

  public PresentationOpenService()
  {
    var root = Path.Combine(global::Windows.Storage.ApplicationData.Current.LocalCacheFolder.Path, "PresentationCache");
    cache = new PresentationCache(root);
  }

  public async Task<PresentationOpenResult> OpenAsync(SiteApiClient api, PresentationOpenRequest request, IProgress<PresentationOpenProgress>? progress, CancellationToken cancellationToken)
  {
    lock (activeDownloads)
    {
      if (!activeDownloads.Add(request.PresentationVersionId)) return new(false, "This presentation version is already downloading.", null);
    }
    try
    {
      progress?.Report(new("PREPARING", null));
      var transferProgress = new Progress<double>(value => progress?.Report(new("DOWNLOADING", value)));
      var cached = await cache.GetOrDownloadAsync(
          new(request.PresentationVersionId, request.Filename, request.ExpectedSize, request.ExpectedSha256),
          (target, reported, token) => api.CopyPresentationVersionAsync(request.PresentationVersionId, target, token, reported),
          transferProgress,
          cancellationToken);
      progress?.Report(new(cached.CacheHit ? "OPENING" : "VERIFYING", 100));
      var file = await global::Windows.Storage.StorageFile.GetFileFromPathAsync(cached.Path);
      progress?.Report(new("OPENING", 100));
      var launched = await global::Windows.System.Launcher.LaunchFileAsync(file);
      return launched
          ? new(true, cached.CacheHit ? "Opened verified cached version." : "Downloaded, verified, and opened.", cached.Path)
          : new(false, "No application is registered to open this presentation type.", cached.Path);
    }
    finally
    {
      lock (activeDownloads) activeDownloads.Remove(request.PresentationVersionId);
    }
  }
}

public sealed record PresentationOpenRequest(Guid PresentationVersionId, string Filename, long? ExpectedSize, string? ExpectedSha256);
public sealed record PresentationOpenProgress(string State, double? Percent);
public sealed record PresentationOpenResult(bool Launched, string Message, string? LocalPath);
