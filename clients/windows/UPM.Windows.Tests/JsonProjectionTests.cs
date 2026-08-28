using System.Net;
using System.Text;
using System.Text.Json;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class JsonProjectionTests
{
  [Fact]
  public void OptionalPresentationMetadataIsNullSafe()
  {
    using var document = JsonDocument.Parse("""{"size_bytes":null,"current_version":null,"starts_at":null,"updated_at":null,"room":null,"presenters":null,"filename":null,"delivery_state":null,"readiness":null}""");
    var item = document.RootElement;

    Assert.Null(item.NullableLong("size_bytes"));
    Assert.Null(item.NullableNumber("current_version"));
    Assert.Null(item.NullableDate("starts_at"));
    Assert.Null(item.NullableDate("updated_at"));
    Assert.Null(item.NullableBool("powerpoint_available"));
    Assert.Equal("—", item.Text("room"));
    Assert.Empty(item.Items("presenters"));
    Assert.Equal("—", item.Text("filename"));
    Assert.Equal("UNKNOWN", item.Text("readiness", "UNKNOWN"));
    Assert.Equal(0, item.NumberOrDefault("size_bytes"));
    Assert.Equal("—", JsonProjection.Bytes(item.NullableLong("size_bytes")));
  }

  [Fact]
  public void PartiallyPopulatedPresentationStillProjects()
  {
    using var document = JsonDocument.Parse("""{"title":"Opening keynote","size_bytes":3145728,"presenters":["Alex"]}""");
    var item = document.RootElement;

    Assert.Equal("Opening keynote", item.Text("title"));
    Assert.Equal("3.0 MB", JsonProjection.Bytes(item.NullableLong("size_bytes")));
    Assert.Single(item.Items("presenters"));
    Assert.Equal("—", item.Text("current_version"));
  }

  [Fact]
  public void MissingRoomAndDeviceTelemetryRemainUnknownRatherThanFabricatedOnline()
  {
    using var document = JsonDocument.Parse("{} ");

    Assert.Equal("UNKNOWN", document.RootElement.Text("online_state", "UNKNOWN"));
    Assert.Equal("UNASSIGNED", document.RootElement.Text("assigned_room_id", "UNASSIGNED"));
    Assert.Null(document.RootElement.NullableDate("last_heartbeat_at"));
  }

  [Fact]
  public async Task ReviewListingExplainsOlderSiteMethodNotAllowed()
  {
    var handler = new StaticHandler(new HttpResponseMessage(HttpStatusCode.MethodNotAllowed));
    var api = new SiteApiClient(
        new HttpClient(handler) { BaseAddress = new Uri("http://site.test:9080/") },
        new CookieContainer());

    var error = await Assert.ThrowsAsync<SiteEndpointException>(
        () => api.GetReviewSessionsAsync(CancellationToken.None));

    Assert.Equal("Review listing requires a newer UPM Site build.", error.Message);
  }

  [Fact]
  public void OperatorContextPropagatesConnectionAndEventWithoutOwningAnotherSession()
  {
    var profile = new SiteProfile(Guid.NewGuid(), "Main", new Uri("http://site.test:9080/"), null, Guid.NewGuid(), "Main", null, DateTimeOffset.UtcNow, DateTimeOffset.UtcNow, null, null);
    var manager = new RaisingConnectionManager();
    using var factory = new SiteClientFactory();
    using var context = new OperatorContext(manager, factory);
    var changes = 0;
    context.Changed += (_, _) => changes++;
    manager.Raise(new SiteConnectionStatus { ProfileId = profile.ProfileId, Profile = profile, State = SiteConnectionState.Connected, Session = new AuthSession() });
    var eventId = Guid.NewGuid();
    context.SelectEvent(eventId);

    Assert.Equal(profile.ProfileId, context.Profile!.ProfileId);
    Assert.Equal(eventId, context.SelectedEventId);
    Assert.NotNull(context.ActiveClient);
    Assert.Equal(2, changes);
  }

  private sealed class RaisingConnectionManager : ISiteConnectionManager
  {
    public event EventHandler<SiteConnectionChangedEventArgs>? ConnectionChanged;
    public SiteConnectionStatus? Current { get; private set; }
    public void Raise(SiteConnectionStatus status) { Current = status; ConnectionChanged?.Invoke(this, new SiteConnectionChangedEventArgs(status)); }
    public SiteConnectionStatus? GetStatus(Guid profileId) => Current;
    public Task<SiteConnectionStatus> TestAsync(SiteProfile profile, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<SiteConnectionStatus> ConnectAsync(SiteProfile profile, string username, string password, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<SiteConnectionStatus> RestoreAsync(SiteProfile profile, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task<SiteOperationalSnapshot> RefreshAsync(Guid profileId, CancellationToken cancellationToken) => throw new NotSupportedException();
    public Task SelectEventAsync(Guid profileId, Guid? eventId, CancellationToken cancellationToken) => Task.CompletedTask;
    public Task LogoutAsync(Guid profileId, CancellationToken cancellationToken) => Task.CompletedTask;
    public Task DisconnectAsync(Guid profileId, CancellationToken cancellationToken) => Task.CompletedTask;
    public Task DeleteProfileAsync(Guid profileId, CancellationToken cancellationToken) => Task.CompletedTask;
  }

  private sealed class StaticHandler(HttpResponseMessage response) : HttpMessageHandler
  {
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken) => Task.FromResult(response);
  }
}
