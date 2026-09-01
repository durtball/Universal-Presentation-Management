using System.Net;
using System.Text;
using Microsoft.Extensions.Logging.Abstractions;
using UPM.Windows.Agent;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class AgentSynchronizationTests
{
  [Fact]
  public async Task ChangeFeedUsesPersistedRevisionsAndBearerCredential()
  {
    HttpRequestMessage? captured = null;
    var handler = new Handler(request =>
    {
      captured = request;
      return new(HttpStatusCode.OK) { Content = new StringContent(EnvelopeJson, Encoding.UTF8, "application/json") };
    });
    var client = new SiteAgentClient(new HttpClient(handler));
    var provisioning = new ProvisioningState(Guid.NewGuid(), Guid.NewGuid(), "ROOM-1", SiteId,
        new Uri("https://site.test/"), EventId, DeviceRole.RoomAgent, RoomId, "Venetian G", DateTimeOffset.UtcNow);

    var result = await client.ChangesAsync(provisioning, "secret", new(11, 12, 13, 14), CancellationToken.None);

    Assert.Equal("Bearer", captured!.Headers.Authorization!.Scheme);
    Assert.Equal("secret", captured.Headers.Authorization.Parameter);
    Assert.Contains("schedule=11", captured.RequestUri!.Query, StringComparison.Ordinal);
    Assert.Contains("rotating_slides=14", captured.RequestUri.Query, StringComparison.Ordinal);
    Assert.Equal("AI4 2026", result.EventName);
  }

  [Fact]
  public async Task DashboardRemainsAvailableAndExplicitlyOfflineFromCachedState()
  {
    using var root = new TemporaryDirectory(); var storage = new AgentStorage(root.Path); storage.EnsureCreated();
    var store = new AgentStateStore(storage.DatabasePath); await store.InitializeAsync();
    await store.SaveProvisioningAsync(new(Guid.NewGuid(), Guid.NewGuid(), "ROOM-1", SiteId,
        new Uri("https://site.test/"), EventId, DeviceRole.RoomAgent, RoomId, "Venetian G", DateTimeOffset.UtcNow,
        "Main Site", "AI4 2026"));
    await store.SaveBrandingAsync(new(0, "Site Managed", string.Empty, null, null, null, null, null, null, null,
        null, null, null, null, DateTimeOffset.UtcNow));

    var dashboard = await new AgentDashboardService(store, storage).GetAsync();

    Assert.True(dashboard.AgentConnected);
    Assert.False(dashboard.SiteConnected);
    Assert.Contains("Using cached", dashboard.SiteStatus, StringComparison.Ordinal);
    Assert.Equal("AI4 2026", dashboard.EventName);
  }

  [Fact]
  public async Task IncrementalAssignmentMoveAndClearReplaceCachedRoomState()
  {
    using var root = new TemporaryDirectory(); var storage = new AgentStorage(root.Path); storage.EnsureCreated();
    var store = new AgentStateStore(storage.DatabasePath); await store.InitializeAsync();
    await store.SaveSettingsAsync(AgentSettings.Default(Path.Combine(root.Path, "library")) with { PresentationLibraryEnabled = false });
    await store.SaveProvisioningAsync(new(Guid.NewGuid(), Guid.NewGuid(), "ROOM-1", SiteId,
        new Uri("https://site.test/"), null, DeviceRole.RoomAgent, null, null, DateTimeOffset.UtcNow, "Main Site"));
    var credentials = new MemoryCredentialStore("secret");
    var responses = new Queue<string>([AssignedEnvelopeJson, MovedEnvelopeJson, ClearedEnvelopeJson]);
    var client = new SiteAgentClient(new HttpClient(new Handler(request =>
        request.RequestUri!.AbsolutePath.EndsWith("heartbeat", StringComparison.Ordinal)
            ? new(HttpStatusCode.OK) { Content = new StringContent("{}", Encoding.UTF8, "application/json") }
            : new(HttpStatusCode.OK) { Content = new StringContent(responses.Dequeue(), Encoding.UTF8, "application/json") })));
    var dashboards = new AgentDashboardService(store, storage);
    var worker = new AgentSyncWorker(store, credentials, client, storage, dashboards, new AgentSyncSignal(),
        new SiteDiscoveryService(), NullLogger<AgentSyncWorker>.Instance);

    await worker.SynchronizeAsync(CancellationToken.None);
    Assert.Equal("AI4 2026", (await store.GetProvisioningAsync())!.EventName);
    Assert.Equal("Venetian G", (await store.GetProvisioningAsync())!.RoomName);
    Assert.Equal("AI4 2026", (await dashboards.GetAsync()).EventName);
    Assert.Equal(0, (await dashboards.GetAsync()).Branding.Revision);
    Assert.Equal("First room session", Assert.Single(await store.ListSessionsAsync(), row => !row.Cancelled).Title);

    await worker.SynchronizeAsync(CancellationToken.None);
    Assert.Equal("Bellini", (await store.GetProvisioningAsync())!.RoomName);
    Assert.Equal("Second room session", Assert.Single(await store.ListSessionsAsync(), row => !row.Cancelled).Title);
    Assert.Contains(await store.ListSessionsAsync(), row => row.Title == "First room session" && row.Cancelled);

    await worker.SynchronizeAsync(CancellationToken.None);
    var cleared = await store.GetProvisioningAsync();
    Assert.Null(cleared!.EventId); Assert.Null(cleared.EventName); Assert.Null(cleared.RoomId);
    Assert.DoesNotContain(await store.ListSessionsAsync(), row => !row.Cancelled);
    Assert.Equal(AgentConnectionPhase.WaitingForAssignment, await store.GetConnectionPhaseAsync());
  }

  [Fact]
  public async Task DurableAgentIdentityIsCreatedOnceAndSurvivesRestart()
  {
    using var root = new TemporaryDirectory(); var path = Path.Combine(root.Path, "agent.db");
    var firstStore = new AgentStateStore(path); await firstStore.InitializeAsync();
    var first = await firstStore.GetOrCreateIdentityAsync();
    var reopened = new AgentStateStore(path); await reopened.InitializeAsync();
    var second = await reopened.GetOrCreateIdentityAsync();
    Assert.Equal(first, second); Assert.Equal(Environment.MachineName, second.MachineName);
  }

  [Fact]
  public void SignedDiscoveryResponseParsesWithoutExposingOperatorInputs()
  {
    var json = $$"""{"site_id":"{{SiteId}}","site_name":"Main Site","endpoint":"http://10.0.0.8:9080/","issued_at":123,"nonce":"0123456789abcdef","signature":"{{new string('a', 64)}}"}""";
    var site = SiteDiscoveryService.Parse(Encoding.UTF8.GetBytes(json));
    Assert.NotNull(site); Assert.Equal(SiteId, site.SiteId); Assert.Equal("10.0.0.8", site.Endpoint.Host);
  }

  private static readonly Guid SiteId = Guid.Parse("019b1111-1111-7111-8111-111111111111");
  private static readonly Guid EventId = Guid.Parse("019b2222-2222-7222-8222-222222222222");
  private static readonly Guid RoomId = Guid.Parse("019b3333-3333-7333-8333-333333333333");
  private static readonly string EnvelopeJson = $$"""
    {"site_id":"{{SiteId}}","site_name":"Main Site","event_id":"{{EventId}}","event_name":"AI4 2026",
    "room_id":"{{RoomId}}","room_name":"Venetian G","role":1,
    "revisions":{"schedule":11,"presentations":12,"branding":13,"rotating_slides":14},"sessions":[],"assets":[],
    "branding":{"revision":0,"source":"Site Managed","event_name":"AI4 2026","assets":[]},"settings":null}
    """;
  private static readonly string AssignedEnvelopeJson = Envelope(1, EventId, "AI4 2026", RoomId, "Venetian G", "First room session");
  private static readonly string MovedEnvelopeJson = Envelope(2, EventId, "AI4 2026", Guid.Parse("019b4444-4444-7444-8444-444444444444"), "Bellini", "Second room session");
  private static readonly string ClearedEnvelopeJson = Envelope(3, null, null, null, null, null);

  private static string Envelope(long revision, Guid? eventId, string? eventName, Guid? roomId, string? roomName, string? sessionTitle)
  {
    var session = sessionTitle is null ? "" : $$"""{"session_id":"{{Guid.CreateVersion7()}}","session_identifier":"123","title":"{{sessionTitle}}","presenter":"Presenter","room_id":"{{roomId}}","room_name":"{{roomName}}","starts_at":"2026-09-01T10:00:00Z","ends_at":"2026-09-01T11:00:00Z","cancelled":false,"revision":{{revision}}}""";
    return $$"""{"site_id":"{{SiteId}}","site_name":"Main Site","event_id":{{(eventId is null ? "null" : $"\"{eventId}\"")}},"event_name":{{(eventName is null ? "null" : $"\"{eventName}\"")}},"room_id":{{(roomId is null ? "null" : $"\"{roomId}\"")}},"room_name":{{(roomName is null ? "null" : $"\"{roomName}\"")}},"role":1,"revisions":{"schedule":{{revision}},"presentations":{{revision}},"branding":0,"rotating_slides":{{revision}}},"sessions":[{{session}}],"assets":[],"branding":{"revision":0,"source":"Site Managed","event_name":"","assets":[]},"settings":null,"assigned":{{(roomId is null ? "false" : "true")}}}""";
  }

  private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
  { protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) => Task.FromResult(response(request)); }
  private sealed class MemoryCredentialStore(string? value) : IAgentCredentialStore
  {
    public Task SaveAsync(string credential, CancellationToken ct = default) { value = credential; return Task.CompletedTask; }
    public Task<string?> ReadAsync(CancellationToken ct = default) => Task.FromResult(value);
    public Task ClearAsync(CancellationToken ct = default) { value = null; return Task.CompletedTask; }
  }
  private sealed class TemporaryDirectory : IDisposable
  { public TemporaryDirectory() => Path = Directory.CreateTempSubdirectory().FullName; public string Path { get; } public void Dispose() => Directory.Delete(Path, true); }
}
