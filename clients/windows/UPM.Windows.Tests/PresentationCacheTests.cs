using System.Security.Cryptography;
using System.Text;
using UPM.Windows.Core;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class PresentationCacheTests
{
  [Fact]
  public async Task MissingVersionDownloadsVerifiesAndThenReusesCache()
  {
    using var root = new TemporaryDirectory();
    var bytes = Encoding.UTF8.GetBytes("canonical presentation bytes");
    var hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    var cache = new PresentationCache(root.Path);
    var request = new PresentationCacheRequest(Guid.NewGuid(), "deck.pptx", bytes.Length, hash);
    var downloads = 0;

    Task Download(Stream target, IProgress<double>? _, CancellationToken cancellationToken)
    {
      downloads++;
      return target.WriteAsync(bytes, cancellationToken).AsTask();
    }

    var first = await cache.GetOrDownloadAsync(request, Download, null, CancellationToken.None);
    var second = await cache.GetOrDownloadAsync(request, Download, null, CancellationToken.None);

    Assert.False(first.CacheHit);
    Assert.True(second.CacheHit);
    Assert.Equal(1, downloads);
    Assert.Equal(bytes, await File.ReadAllBytesAsync(second.Path));
  }

  [Fact]
  public async Task VersionIdentityPreventsStaleCacheReuse()
  {
    using var root = new TemporaryDirectory();
    var cache = new PresentationCache(root.Path);
    var firstBytes = "v1"u8.ToArray();
    var secondBytes = "v2"u8.ToArray();
    static string Hash(byte[] value) => Convert.ToHexString(SHA256.HashData(value)).ToLowerInvariant();

    var first = await cache.GetOrDownloadAsync(
        new(Guid.NewGuid(), "same-name.pptx", firstBytes.Length, Hash(firstBytes)),
        (target, _, token) => target.WriteAsync(firstBytes, token).AsTask(), null, CancellationToken.None);
    var second = await cache.GetOrDownloadAsync(
        new(Guid.NewGuid(), "same-name.pptx", secondBytes.Length, Hash(secondBytes)),
        (target, _, token) => target.WriteAsync(secondBytes, token).AsTask(), null, CancellationToken.None);

    Assert.NotEqual(first.Path, second.Path);
    Assert.Equal(secondBytes, await File.ReadAllBytesAsync(second.Path));
  }

  [Fact]
  public async Task InterruptedOrInvalidDownloadNeverBecomesReady()
  {
    using var root = new TemporaryDirectory();
    var cache = new PresentationCache(root.Path);
    var request = new PresentationCacheRequest(Guid.NewGuid(), "deck.pptx", 20, new string('a', 64));

    await Assert.ThrowsAsync<InvalidDataException>(() => cache.GetOrDownloadAsync(
        request,
        (target, _, token) => target.WriteAsync("partial"u8.ToArray(), token).AsTask(),
        null,
        CancellationToken.None));

    Assert.Empty(Directory.GetFiles(root.Path, "*", SearchOption.AllDirectories));
  }

  private sealed class TemporaryDirectory : IDisposable
  {
    public string Path { get; } = Directory.CreateTempSubdirectory().FullName;
    public void Dispose() => Directory.Delete(Path, true);
  }
}
