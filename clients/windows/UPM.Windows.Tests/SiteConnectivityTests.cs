using System.Net;
using System.Text;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class SiteConnectivityTests
{
  [Theory]
  [InlineData("192.168.1.10", null, "http://192.168.1.10:9080/")]
  [InlineData("192.168.1.10", 9090, "http://192.168.1.10:9090/")]
  [InlineData("192.168.1.10:9081", null, "http://192.168.1.10:9081/")]
  [InlineData("http://site.local:9080/path", null, "http://site.local:9080/")]
  [InlineData("https://site.company.local", null, "https://site.company.local/")]
  public void SiteAddressIsNormalized(string address, int? port, string expected)
  {
    Assert.Equal(expected, SiteAddressNormalizer.Normalize(address, port).AbsoluteUri);
  }

  [Fact]
  public async Task SnakeCaseAuthAndConnectionDtosDeserialize()
  {
    var handler = new RecordingHandler(request => request.RequestUri!.AbsolutePath switch
    {
      "/api/v1/auth/session" => Json("""{"authenticated":true,"user":{"user_id":"019b1111-1111-7111-8111-111111111111","username":"operator","display_name":"Show Operator","roles":["operator"],"permissions":["presentations.read"]},"csrf_token":"csrf-value"}"""),
      "/api/v1/central-registration" => Json("""{"site_id":"019b2222-2222-7222-8222-222222222222","display_name":"Main Site","registration_state":"registered","connection_status":"connected","last_connection_at":null}"""),
      "/api/v1/event-deployments" => Json("""[{"deployment_id":"019b3333-3333-7333-8333-333333333333","central_event_id":"019b4444-4444-7444-8444-444444444444","site_id":"019b2222-2222-7222-8222-222222222222","event_name":"Annual Show","status":"ready","desired_revision":3,"applied_revision":3,"failure_reason":null}]"""),
      _ => throw new InvalidOperationException(request.RequestUri.AbsolutePath),
    });
    var api = CreateApi(handler);

    var session = await api.RestoreSessionAsync(CancellationToken.None);
    var registration = await api.GetRegistrationAsync(CancellationToken.None);
    var deployments = await api.GetEventDeploymentsAsync(CancellationToken.None);

    Assert.Equal("Show Operator", session!.User.DisplayName);
    Assert.Equal("csrf-value", session.CsrfToken);
    Assert.Equal(Guid.Parse("019b2222-2222-7222-8222-222222222222"), registration.SiteId);
    Assert.Equal("Annual Show", Assert.Single(deployments).EventName);
    Assert.Equal(Guid.Parse("019b4444-4444-7444-8444-444444444444"), deployments[0].EventId);
  }

  [Fact]
  public async Task ClientsHaveProfileSpecificBaseAddressesAndIndependentCookies()
  {
    using var factory = new SiteClientFactory();
    var first = Profile(new Uri("http://site-a:9080/"));
    var second = Profile(new Uri("https://site-b/"));

    var firstClient = factory.GetClient(first);
    var secondClient = factory.GetClient(second);
    firstClient.Api.RestoreSessionCookie("site-a-token");

    Assert.Equal(first.BaseUri, firstClient.HttpClient.BaseAddress);
    Assert.Equal(second.BaseUri, secondClient.HttpClient.BaseAddress);
    Assert.Equal("site-a-token", firstClient.Api.GetSessionCookie());
    Assert.Null(secondClient.Api.GetSessionCookie());
    Assert.NotSame(firstClient.Cookies, secondClient.Cookies);
  }

  [Fact]
  public async Task SessionRestoreAddsFreshCsrfAndExpiredSessionIsReported()
  {
    var profile = Profile(new Uri("http://site-a:9080/")) with
    {
      CanonicalSiteId = Guid.Parse("019b2222-2222-7222-8222-222222222222"),
    };
    var vault = new MemoryCredentialVault();
    await vault.SaveAsync(profile.ProfileId, "secure-token", CancellationToken.None);
    var validFactory = new FakeFactory(profile, CompleteSiteHandler(sessionStatus: HttpStatusCode.OK));
    var store = await CreateStoreAsync(profile);
    var manager = new SiteConnectionManager(store, vault, validFactory, NullLogger<SiteConnectionManager>.Instance);

    var connected = await manager.RestoreAsync(profile, CancellationToken.None);

    Assert.Equal(SiteConnectionState.Connected, connected.State);
    Assert.Equal("fresh-csrf", validFactory.Context.Api.CsrfToken);

    var expiredVault = new MemoryCredentialVault();
    await expiredVault.SaveAsync(profile.ProfileId, "expired", CancellationToken.None);
    var expiredFactory = new FakeFactory(profile, CompleteSiteHandler(sessionStatus: HttpStatusCode.Unauthorized));
    var expiredManager = new SiteConnectionManager(store, expiredVault, expiredFactory, NullLogger<SiteConnectionManager>.Instance);

    var expired = await expiredManager.RestoreAsync(profile, CancellationToken.None);

    Assert.Equal(SiteConnectionState.SessionExpired, expired.State);
    Assert.Null(await expiredVault.ReadAsync(profile.ProfileId, CancellationToken.None));
  }

  [Fact]
  public async Task CsrfIsAttachedAndCanonicalSiteIdNotProfileIdIsUsedForUpload()
  {
    var handler = new RecordingHandler(request =>
    {
      if (request.RequestUri!.AbsolutePath == "/api/v1/auth/session")
      {
        return Json(SessionJson);
      }

      return new HttpResponseMessage(HttpStatusCode.Created) { Content = JsonContent("{}") };
    });
    var api = CreateApi(handler);
    await api.RestoreSessionAsync(CancellationToken.None);
    var profileId = Guid.NewGuid();
    var canonicalId = Guid.NewGuid();
    var item = Transfer(profileId);

    using var response = await api.UploadAsync(item, canonicalId, new MemoryStream([1, 2, 3]), CancellationToken.None);
    var upload = handler.Requests.Last();

    Assert.Contains($"site_id={canonicalId}", upload.RequestUri!.Query, StringComparison.Ordinal);
    Assert.DoesNotContain($"site_id={profileId}", upload.RequestUri.Query, StringComparison.Ordinal);
    Assert.Equal("fresh-csrf", upload.Headers.GetValues("X-CSRF-Token").Single());
  }

  [Fact]
  public async Task IdempotentUploadRefreshesCsrfOnceAfterForbidden()
  {
    var uploadCount = 0;
    var sessionCount = 0;
    var handler = new RecordingHandler(request =>
    {
      if (request.RequestUri!.AbsolutePath == "/api/v1/auth/session")
      {
        sessionCount++;
        return Json(SessionJson.Replace("fresh-csrf", $"csrf-{sessionCount}", StringComparison.Ordinal));
      }

      uploadCount++;
      return new HttpResponseMessage(
              uploadCount == 1 ? HttpStatusCode.Forbidden : HttpStatusCode.Created)
      {
        Content = JsonContent("{}"),
      };
    });
    var api = CreateApi(handler);
    await api.RestoreSessionAsync(CancellationToken.None);

    using var response = await api.UploadAsync(
        Transfer(Guid.NewGuid()),
        Guid.NewGuid(),
        new MemoryStream([1, 2, 3]),
        CancellationToken.None);

    Assert.Equal(HttpStatusCode.Created, response.StatusCode);
    Assert.Equal(2, uploadCount);
    Assert.Equal(2, sessionCount);
    Assert.Equal("csrf-2", handler.Requests.Last().Headers.GetValues("X-CSRF-Token").Single());
  }

  [Fact]
  public async Task IdentityMismatchIsRejectedAndTransferKeepsOwningProfile()
  {
    var expectedSite = Guid.NewGuid();
    var profile = Profile(new Uri("http://site-a:9080/")) with { CanonicalSiteId = expectedSite };
    var store = await CreateStoreAsync(profile);
    var vault = new MemoryCredentialVault();
    await vault.SaveAsync(profile.ProfileId, "token", CancellationToken.None);
    var reportedSite = Guid.NewGuid();
    var handler = CompleteSiteHandler(sessionStatus: HttpStatusCode.OK, siteId: reportedSite);
    var factory = new FakeFactory(profile, handler);
    var router = new SiteTransferRouter(store, vault, factory);
    var item = Transfer(profile.ProfileId);

    var error = await Assert.ThrowsAsync<SiteEndpointException>(
        () => TransferWorker.ResolveDestinationAsync(item, router, CancellationToken.None));

    Assert.Equal(SiteConnectionState.IdentityMismatch, error.State);
    Assert.Equal(profile.ProfileId, item.SiteProfileId);
  }

  [Fact]
  public async Task TransferResolutionUsesOwningProfileNotUiSelection()
  {
    var owningProfile = Guid.NewGuid();
    var selectedUiProfile = Guid.NewGuid();
    var router = new CapturingTransferRouter();
    var item = Transfer(owningProfile);

    _ = selectedUiProfile;
    await TransferWorker.ResolveDestinationAsync(item, router, CancellationToken.None);

    Assert.Equal(owningProfile, router.ResolvedProfileId);
    Assert.NotEqual(selectedUiProfile, router.ResolvedProfileId);
  }

  [Fact]
  public async Task ProfileDeletionRemovesSecureCredential()
  {
    var profile = Profile(new Uri("http://site-a:9080/"));
    var store = await CreateStoreAsync(profile);
    var vault = new MemoryCredentialVault();
    await vault.SaveAsync(profile.ProfileId, "token", CancellationToken.None);
    var factory = new FakeFactory(profile, CompleteSiteHandler(HttpStatusCode.Unauthorized));
    var manager = new SiteConnectionManager(store, vault, factory, NullLogger<SiteConnectionManager>.Instance);

    await manager.DeleteProfileAsync(profile.ProfileId, CancellationToken.None);

    Assert.Null(await vault.ReadAsync(profile.ProfileId, CancellationToken.None));
    Assert.Null(await store.GetSiteProfileAsync(profile.ProfileId));
    Assert.Contains(profile.ProfileId, vault.Forgotten);
  }

  [Fact]
  public async Task SiteProgramAndPresentationOperationsUseAuthenticatedSiteRoutes()
  {
    var handler = new RecordingHandler(request => request.RequestUri!.AbsolutePath switch
    {
      "/api/v1/auth/session" => Json(SessionJson),
      "/api/v1/events" => Json("""{"event_id":"019b4444-4444-7444-8444-444444444444"}"""),
      "/api/v1/events/019b4444-4444-7444-8444-444444444444/program-imports" =>
          Json("""{"import_batch_id":"019b5555-5555-7555-8555-555555555555"}"""),
      "/api/v1/program-imports/019b5555-5555-7555-8555-555555555555/commit" =>
          Json("""{"status":"committed"}"""),
      "/api/v1/presentations/019b6666-6666-7666-8666-666666666666/assignment" =>
          Json("""{"revision":2}"""),
      _ => throw new InvalidOperationException(request.RequestUri.AbsolutePath),
    });
    var api = CreateApi(handler);
    await api.RestoreSessionAsync(CancellationToken.None);
    var eventId = Guid.Parse("019b4444-4444-7444-8444-444444444444");
    var batchId = Guid.Parse("019b5555-5555-7555-8555-555555555555");
    var presentationId = Guid.Parse("019b6666-6666-7666-8666-666666666666");

    await api.CreateEventAsync("Offline Show", "UTC", CancellationToken.None);
    await api.UploadProgramImportAsync(
        eventId,
        "program.csv",
        new MemoryStream("Session Title\nOpening"u8.ToArray()),
        CancellationToken.None);
    await api.CommitProgramImportAsync(batchId, CancellationToken.None);
    await api.UpdatePresentationAssignmentAsync(
        presentationId,
        Guid.NewGuid(),
        1,
        CancellationToken.None);

    Assert.All(
        handler.Requests.Where(request => request.Method != HttpMethod.Get),
        request => Assert.Equal(
            "fresh-csrf",
            request.Headers.GetValues("X-CSRF-Token").Single()));
  }

  private const string SessionJson = """{"authenticated":true,"user":{"user_id":"019b1111-1111-7111-8111-111111111111","username":"operator","display_name":"Operator","roles":[],"permissions":[]},"csrf_token":"fresh-csrf"}""";

  private static RecordingHandler CompleteSiteHandler(HttpStatusCode sessionStatus, Guid? siteId = null)
  {
    var canonical = siteId ?? Guid.Parse("019b2222-2222-7222-8222-222222222222");
    return new RecordingHandler(request => request.RequestUri!.AbsolutePath switch
    {
      "/health" => Json("""{"service":"upm-site","status":"foundation-ready"}"""),
      "/api/v1/auth/session" when sessionStatus == HttpStatusCode.Unauthorized => new HttpResponseMessage(HttpStatusCode.Unauthorized),
      "/api/v1/auth/session" => Json(SessionJson),
      "/api/v1/central-registration" => Json($$"""{"site_id":"{{canonical}}","display_name":"Main Site","registration_state":"registered","connection_status":"connected","last_connection_at":null}"""),
      "/api/v1/event-deployments" => Json("[]"),
      "/api/v1/media-storage" => Json("""{"service_available":true,"roots":[]}"""),
      "/api/v1/devices" => Json("[]"),
      "/api/v1/operations/dashboard" => Json("""{"rooms":[],"attention":[],"failed_transfer_jobs":0}"""),
      _ => throw new InvalidOperationException(request.RequestUri.AbsolutePath),
    });
  }

  private static SiteApiClient CreateApi(RecordingHandler handler)
  {
    var baseUri = new Uri("http://site.test:9080/");
    return new SiteApiClient(new HttpClient(handler) { BaseAddress = baseUri }, new CookieContainer());
  }

  private static SiteProfile Profile(Uri uri)
  {
    var now = DateTimeOffset.UtcNow;
    return new SiteProfile(Guid.NewGuid(), "Test Site", uri, "operator", null, null, null, now, now, null, null);
  }

  private static TransferItem Transfer(Guid profileId) => new(
      Guid.NewGuid(), profileId, null, "deck.pptx", "deck.pptx", "deck.pptx", null, 3,
      DateTimeOffset.UtcNow, "idempotency-key");

  private static async Task<LocalStateStore> CreateStoreAsync(SiteProfile profile)
  {
    var root = Directory.CreateTempSubdirectory();
    var store = new LocalStateStore(Path.Combine(root.FullName, "state.db"));
    await store.InitializeAsync();
    await store.UpsertSiteProfileAsync(profile);
    return store;
  }

  private static HttpResponseMessage Json(string json) => new(HttpStatusCode.OK) { Content = JsonContent(json) };
  private static StringContent JsonContent(string json) => new(json, Encoding.UTF8, "application/json");

  private sealed class RecordingHandler(Func<HttpRequestMessage, HttpResponseMessage> response) : HttpMessageHandler
  {
    public List<HttpRequestMessage> Requests { get; } = [];

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
      Requests.Add(request);
      return Task.FromResult(response(request));
    }
  }

  private sealed class FakeFactory : ISiteClientFactory
  {
    public FakeFactory(SiteProfile profile, HttpMessageHandler handler)
    {
      Context = new SiteClientContext(
          profile,
          new HttpClient(handler) { BaseAddress = profile.BaseUri },
          new CookieContainer());
    }

    public SiteClientContext Context { get; }
    public SiteClientContext GetClient(SiteProfile profile) => Context;
    public void Remove(Guid profileId) => Context.Api.ClearSession();
  }

  private sealed class MemoryCredentialVault : ICredentialVault
  {
    private readonly Dictionary<Guid, string> values = [];
    public HashSet<Guid> Forgotten { get; } = [];
    public ValueTask SaveAsync(Guid profileId, string sessionCookie, CancellationToken cancellationToken)
    {
      values[profileId] = sessionCookie;
      return ValueTask.CompletedTask;
    }

    public ValueTask<string?> ReadAsync(Guid profileId, CancellationToken cancellationToken) =>
        ValueTask.FromResult(values.GetValueOrDefault(profileId));

    public ValueTask ForgetAsync(Guid profileId, CancellationToken cancellationToken)
    {
      values.Remove(profileId);
      Forgotten.Add(profileId);
      return ValueTask.CompletedTask;
    }

  }

  private sealed class CapturingTransferRouter : ISiteTransferRouter
  {
    public Guid ResolvedProfileId { get; private set; }

    public Task<SiteTransferDestination> ResolveAsync(
        Guid profileId,
        CancellationToken cancellationToken)
    {
      ResolvedProfileId = profileId;
      var profile = Profile(new Uri("http://owning-site:9080/")) with { ProfileId = profileId };
      var api = CreateApi(new RecordingHandler(_ => Json("{}")));
      return Task.FromResult(new SiteTransferDestination(profile, Guid.NewGuid(), api));
    }
  }
}
