using System.Net;
using System.Text;
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
        new Uri("https://site.test/"), EventId, DeviceRole.RoomAgent, RoomId, "Venetian G", DateTimeOffset.UtcNow));
    await store.SaveBrandingAsync(new(3, "Site Managed", "AI4 2026", null, null, null, null, null, null, null,
        null, null, null, null, DateTimeOffset.UtcNow));

    var dashboard = await new AgentDashboardService(store, storage).GetAsync();

    Assert.True(dashboard.AgentConnected);
    Assert.False(dashboard.SiteConnected);
    Assert.Contains("Using cached data", dashboard.SiteStatus, StringComparison.Ordinal);
    Assert.Equal("AI4 2026", dashboard.EventName);
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

  private sealed class Handler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
  { protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct) => Task.FromResult(response(request)); }
  private sealed class TemporaryDirectory : IDisposable
  { public TemporaryDirectory() => Path = Directory.CreateTempSubdirectory().FullName; public string Path { get; } public void Dispose() => Directory.Delete(Path, true); }
}
