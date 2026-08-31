using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Channels;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace UPM.Windows.Agent;

public sealed class AgentSyncSignal
{
  private readonly Channel<bool> requests = Channel.CreateBounded<bool>(new BoundedChannelOptions(1) { FullMode = BoundedChannelFullMode.DropWrite });
  public bool Request() => requests.Writer.TryWrite(true);
  public async Task WaitAsync(TimeSpan fallback, CancellationToken ct)
  {
    using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct); timeout.CancelAfter(fallback);
    try { await requests.Reader.ReadAsync(timeout.Token); }
    catch (OperationCanceledException) when (!ct.IsCancellationRequested) { }
  }
}

public sealed class SiteAgentClient(HttpClient http)
{
  private static readonly JsonSerializerOptions SiteJson = new(JsonSerializerDefaults.Web)
  {
    PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
  };

  public async Task<SiteSyncEnvelope> BootstrapAsync(Uri site, string credential, CancellationToken ct)
  {
    using var request = Request(site, "api/v1/agent/bootstrap", credential);
    using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
    response.EnsureSuccessStatusCode();
    return await response.Content.ReadFromJsonAsync<SiteSyncEnvelope>(SiteJson, ct)
        ?? throw new InvalidDataException("Site returned an empty Agent bootstrap.");
  }

  public async Task<SiteSyncEnvelope> ChangesAsync(ProvisioningState p, string credential, SyncRevisions revisions, CancellationToken ct)
  {
    var path = $"api/v1/agent/changes?schedule={revisions.Schedule}&presentations={revisions.Presentations}&branding={revisions.Branding}&rotating_slides={revisions.RotatingSlides}";
    using var request = Request(p.SiteAddress, path, credential);
    using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
    response.EnsureSuccessStatusCode();
    return await response.Content.ReadFromJsonAsync<SiteSyncEnvelope>(SiteJson, ct)
        ?? throw new InvalidDataException("Site returned an empty Agent change envelope.");
  }

  public async Task<Stream> DownloadAsync(Uri site, Uri download, string credential, CancellationToken ct)
  {
    var uri = download.IsAbsoluteUri ? download : new Uri(site, download);
    using var request = new HttpRequestMessage(HttpMethod.Get, uri);
    request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", credential);
    var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
    if (!response.IsSuccessStatusCode) { response.Dispose(); response.EnsureSuccessStatusCode(); }
    return new ResponseStream(await response.Content.ReadAsStreamAsync(ct), response);
  }

  public async Task HeartbeatAsync(ProvisioningState p, string credential, AgentDashboard dashboard, CancellationToken ct)
  {
    using var request = Request(p.SiteAddress, "api/v1/agent/heartbeat", credential, HttpMethod.Post);
    request.Content = JsonContent.Create(new
    {
      hostname = Environment.MachineName,
      display_name = p.DeviceName,
      windows_version = dashboard.WindowsVersion,
      agent_version = dashboard.AgentVersion,
      interactive_session_available = true,
      powerpoint_available = dashboard.PowerPointDetected,
      free_disk_bytes = dashboard.FreeDiskBytes,
      local_cache_bytes = dashboard.CacheBytes,
      current_presentation_id = dashboard.CurrentSession?.Presentation?.PresentationId,
      metadata = new
      {
        role = p.Role.ToString(),
        event_id = p.EventId,
        room_id = p.RoomId,
        last_sync = dashboard.LastSiteSync,
        presentation_ready = dashboard.CurrentSession?.Presentation?.Readiness == ReadinessState.Ready,
        failed_transfers = dashboard.FailedTransfers,
        branding_revision = dashboard.Branding.Revision,
        kiosk_enabled = dashboard.Settings.KioskEnabled
      }
    }, options: SiteJson);
    using var response = await http.SendAsync(request, ct); response.EnsureSuccessStatusCode();
  }

  private static HttpRequestMessage Request(Uri site, string path, string credential, HttpMethod? method = null)
  { var result = new HttpRequestMessage(method ?? HttpMethod.Get, new Uri(site, path)); result.Headers.Authorization = new("Bearer", credential); return result; }
  private sealed class ResponseStream(Stream stream, HttpResponseMessage response) : Stream
  {
    public override bool CanRead => stream.CanRead; public override bool CanSeek => false; public override bool CanWrite => false; public override long Length => stream.Length;
    public override long Position { get => stream.Position; set => throw new NotSupportedException(); }
    public override void Flush() => stream.Flush(); public override int Read(byte[] b, int o, int c) => stream.Read(b, o, c);
    public override Task<int> ReadAsync(byte[] b, int o, int c, CancellationToken ct) => stream.ReadAsync(b.AsMemory(o, c), ct).AsTask();
    public override ValueTask<int> ReadAsync(Memory<byte> b, CancellationToken ct = default) => stream.ReadAsync(b, ct);
    protected override void Dispose(bool disposing) { if (disposing) { stream.Dispose(); response.Dispose(); } base.Dispose(disposing); }
    public override long Seek(long o, SeekOrigin so) => throw new NotSupportedException(); public override void SetLength(long v) => throw new NotSupportedException(); public override void Write(byte[] b, int o, int c) => throw new NotSupportedException();
  }
}

public sealed class AgentSyncWorker(
    AgentStateStore state, IAgentCredentialStore credentials, SiteAgentClient site,
    AgentStorage storage, AgentDashboardService dashboards, AgentSyncSignal signal,
    ILogger<AgentSyncWorker> logger) : BackgroundService
{
  private readonly VerifiedTransferEngine transfers = new(storage);
  protected override async Task ExecuteAsync(CancellationToken stoppingToken)
  {
    while (!stoppingToken.IsCancellationRequested)
    {
      try { await SynchronizeAsync(stoppingToken); }
      catch (Exception exception) when (exception is not OperationCanceledException)
      { await state.SetSiteConnectedAsync(false, stoppingToken); logger.LogWarning(exception, "Agent Site synchronization failed; cached state remains active"); }
      await signal.WaitAsync(TimeSpan.FromSeconds(30), stoppingToken);
    }
  }

  public async Task ProvisionAsync(ProvisioningRequest request, CancellationToken ct)
  {
    var envelope = await site.BootstrapAsync(request.SiteAddress, request.EnrollmentCredential, ct);
    await credentials.SaveAsync(request.EnrollmentCredential, ct);
    await state.SaveProvisioningAsync(new(Guid.CreateVersion7(), request.DeviceId, request.DeviceName, envelope.SiteId, request.SiteAddress,
        envelope.EventId, envelope.Role, envelope.RoomId, envelope.RoomName, DateTimeOffset.UtcNow, envelope.SiteName), ct);
    await ApplyAsync(envelope, request.EnrollmentCredential, request.SiteAddress, ct);
    signal.Request();
  }

  public async Task SynchronizeAsync(CancellationToken ct)
  {
    var p = await state.GetProvisioningAsync(ct); var credential = await credentials.ReadAsync(ct);
    if (p is null || string.IsNullOrEmpty(credential)) return;
    var envelope = await site.ChangesAsync(p, credential, await state.GetRevisionsAsync(ct), ct);
    await ApplyAsync(envelope, credential, p.SiteAddress, ct);
    await site.HeartbeatAsync(p, credential, await dashboards.GetAsync(ct: ct), ct);
  }

  private async Task ApplyAsync(SiteSyncEnvelope envelope, string credential, Uri siteAddress, CancellationToken ct)
  {
    var priorSessions = await state.ListSessionsAsync(ct);
    var incomingIds = envelope.Sessions.Select(row => row.SessionId).ToHashSet();
    if (envelope.Revisions.Schedule > (await state.GetRevisionsAsync(ct)).Schedule)
      foreach (var removed in priorSessions.Where(row => !row.Cancelled && !incomingIds.Contains(row.SessionId)))
        await state.UpsertSessionAsync(removed with { Cancelled = true, Revision = removed.Revision + 1 }, ct);
    foreach (var session in envelope.Sessions) await state.UpsertSessionAsync(session, ct);
    var sessions = await state.ListSessionsAsync(ct);
    var settings = envelope.Settings ?? await state.GetSettingsAsync(ct) ?? AgentSettings.Default(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "UPM Presentations"));
    await state.SaveSettingsAsync(settings, ct);
    var library = new PresentationLibrary(settings.PresentationLibraryPath);
    foreach (var descriptor in envelope.Assets)
    {
      var asset = await state.GetVerifiedVersionAsync(descriptor.VersionId, ct);
      if (asset is null)
      {
        await using var stream = await site.DownloadAsync(siteAddress, descriptor.DownloadUri, credential, ct);
        var path = await transfers.DownloadAsync(new(descriptor.VersionId, descriptor.OriginalFilename, descriptor.Sha256, descriptor.Size), stream, ct);
        asset = new AgentAsset(descriptor.AssetId, descriptor.Kind, descriptor.VersionId, descriptor.PresentationId, descriptor.SessionId, descriptor.RoomId,
            descriptor.EventDay, descriptor.RotationScope, descriptor.OriginalFilename, Path.GetFileName(path), descriptor.Sha256, descriptor.Size, path, true, true, DateTimeOffset.UtcNow);
        await state.UpsertAssetAsync(asset, ct);
      }
      if (settings.PresentationLibraryEnabled)
      {
        var oldPath = await state.GetLibraryPathAsync(asset.AssetId, asset.SessionId, ct);
        var visible = await library.PublishAsync(asset, sessions.FirstOrDefault(row => row.SessionId == asset.SessionId), ct);
        await state.SetLibraryPathAsync(asset.AssetId, asset.SessionId, visible, ct);
        if (oldPath is not null && !oldPath.Equals(visible, StringComparison.OrdinalIgnoreCase) && File.Exists(oldPath))
        {
          File.Delete(oldPath); RemoveEmptyParents(Path.GetDirectoryName(oldPath), settings.PresentationLibraryPath);
        }
      }
    }
    await ApplyBrandingAsync(envelope.Branding, siteAddress, credential, ct);
    await state.SaveRevisionsAsync(envelope.Revisions, ct); await state.SetLastSuccessfulSyncAsync(DateTimeOffset.UtcNow, ct); await state.SetSiteConnectedAsync(true, ct);
  }

  private static void RemoveEmptyParents(string? path, string root)
  {
    var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar);
    while (path is not null && Path.GetFullPath(path).StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase) && !Path.GetFullPath(path).Equals(fullRoot, StringComparison.OrdinalIgnoreCase))
    {
      if (Directory.EnumerateFileSystemEntries(path).Any()) break;
      Directory.Delete(path); path = Path.GetDirectoryName(path);
    }
  }

  private async Task ApplyBrandingAsync(BrandingManifest manifest, Uri address, string credential, CancellationToken ct)
  {
    var current = await state.GetBrandingAsync(ct); if (current?.Revision == manifest.Revision) return;
    var staging = Path.Combine(storage.Downloads, $"branding-{manifest.Revision}"); Directory.CreateDirectory(staging);
    var paths = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
    try
    {
      foreach (var asset in manifest.Assets)
      {
        var path = WindowsPathPolicy.EnsureContained(staging, WindowsPathPolicy.UploadedFilename(asset.OriginalFilename));
        await using var input = await site.DownloadAsync(address, asset.DownloadUri, credential, ct); await using var output = File.Create(path); await input.CopyToAsync(output, ct); await output.FlushAsync(ct);
        await using var verify = File.OpenRead(path); var hash = Convert.ToHexString(await SHA256.HashDataAsync(verify, ct)).ToLowerInvariant();
        if (new FileInfo(path).Length != asset.Size || !hash.Equals(asset.Sha256, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Branding asset verification failed.");
        paths[asset.Slot] = path;
      }
      var active = Path.Combine(storage.Branding, manifest.Revision.ToString()); if (Directory.Exists(active)) Directory.Delete(active, true); Directory.Move(staging, active);
      string? Slot(string name) => paths.TryGetValue(name, out var value) ? Path.Combine(active, Path.GetFileName(value)) : null;
      await state.SaveBrandingAsync(new(manifest.Revision, manifest.Source, manifest.EventName, Slot("event-logo"), Slot("client-logo"), Slot("kiosk-logo"), Slot("kiosk-background"), Slot("room-client-background"), manifest.AccentColor, manifest.PrimaryColor, manifest.WelcomeMessage, manifest.UploadInstructions, manifest.Footer, Slot("sponsor"), DateTimeOffset.UtcNow), ct);
    }
    catch { if (Directory.Exists(staging)) Directory.Delete(staging, true); throw; }
  }
}
