using System.Net;
using System.Net.Http.Json;
using System.Net.Security;
using System.Security.Authentication;
using System.Text.Json;
using System.Text.Json.Serialization;
using UPM.Windows.Core;

namespace UPM.Windows.SiteApi;

public sealed class SiteUser
{
  [JsonPropertyName("user_id")]
  public Guid UserId { get; set; }

  [JsonPropertyName("username")]
  public string Username { get; set; } = string.Empty;

  [JsonPropertyName("display_name")]
  public string DisplayName { get; set; } = string.Empty;

  [JsonPropertyName("roles")]
  public string[] Roles { get; set; } = [];

  [JsonPropertyName("permissions")]
  public string[] Permissions { get; set; } = [];
}

public class AuthSession
{
  [JsonPropertyName("authenticated")]
  public bool Authenticated { get; set; }

  [JsonPropertyName("user")]
  public SiteUser User { get; set; } = new();

  [JsonPropertyName("csrf_token")]
  public string CsrfToken { get; set; } = string.Empty;
}

public sealed class LoginResult : AuthSession;

public sealed class SiteHealth
{
  [JsonPropertyName("service")]
  public string Service { get; set; } = string.Empty;

  [JsonPropertyName("status")]
  public string Status { get; set; } = string.Empty;
}

public sealed class SiteRegistration
{
  [JsonPropertyName("site_id")]
  public Guid SiteId { get; set; }

  [JsonPropertyName("display_name")]
  public string DisplayName { get; set; } = string.Empty;

  [JsonPropertyName("registration_state")]
  public string RegistrationState { get; set; } = string.Empty;

  [JsonPropertyName("connection_status")]
  public string ConnectionStatus { get; set; } = string.Empty;

  [JsonPropertyName("last_connection_at")]
  public DateTimeOffset? LastConnectionAt { get; set; }
}

public sealed class EventDeployment
{
  [JsonPropertyName("deployment_id")]
  public Guid DeploymentId { get; set; }

  [JsonPropertyName("central_event_id")]
  // Deployment snapshots preserve the canonical Event UUID when materialized into Site.Event.
  public Guid EventId { get; set; }

  [JsonPropertyName("site_id")]
  public Guid SiteId { get; set; }

  [JsonPropertyName("event_name")]
  public string? EventName { get; set; }

  [JsonPropertyName("status")]
  public string Status { get; set; } = string.Empty;

  [JsonPropertyName("desired_revision")]
  public int DesiredRevision { get; set; }

  [JsonPropertyName("applied_revision")]
  public int AppliedRevision { get; set; }

  [JsonPropertyName("failure_reason")]
  public string? FailureReason { get; set; }

  public string DisplayLabel => $"{EventName ?? EventId.ToString()} — {Status}";
}

public sealed record DeviceCommandRequest(
    [property: JsonPropertyName("device_id")] Guid DeviceId,
    [property: JsonPropertyName("room_id")] Guid? RoomId,
    [property: JsonPropertyName("command_type")] string CommandType,
    [property: JsonPropertyName("payload")] Dictionary<string, object> Payload,
    [property: JsonPropertyName("idempotency_key")] string IdempotencyKey,
    [property: JsonPropertyName("expires_at")] DateTimeOffset? ExpiresAt = null,
    [property: JsonPropertyName("correlation_id")] Guid? CorrelationId = null);

public sealed class SiteEndpointException(string message, SiteConnectionState state, Exception? inner = null)
    : Exception(message, inner)
{
  public SiteConnectionState State { get; } = state;
}

public enum SiteConnectionState
{
  Disconnected,
  Connecting,
  Reachable,
  Authenticating,
  Connected,
  AuthenticationRequired,
  SessionExpired,
  Unreachable,
  IdentityMismatch,
  Error,
}

public sealed class SiteClientContext : IDisposable
{
  public SiteClientContext(SiteProfile profile, HttpClient httpClient, CookieContainer cookies)
  {
    Profile = profile;
    HttpClient = httpClient;
    Cookies = cookies;
    Api = new SiteApiClient(httpClient, cookies);
  }

  public SiteProfile Profile { get; }
  public HttpClient HttpClient { get; }
  public CookieContainer Cookies { get; }
  public SiteApiClient Api { get; }

  public void Dispose() => HttpClient.Dispose();
}

public interface ISiteClientFactory
{
  SiteClientContext GetClient(SiteProfile profile);
  void Remove(Guid profileId);
}

public sealed class SiteClientFactory(
    Func<SiteProfile, HttpMessageHandler>? handlerFactory = null,
    TimeSpan? timeout = null) : ISiteClientFactory, IDisposable
{
  private readonly Dictionary<Guid, SiteClientContext> clients = [];
  private readonly object sync = new();

  public SiteClientContext GetClient(SiteProfile profile)
  {
    lock (sync)
    {
      if (clients.TryGetValue(profile.ProfileId, out var existing) &&
          existing.Profile.BaseUri == profile.BaseUri &&
          existing.Profile.CertificateThumbprint == profile.CertificateThumbprint)
      {
        return existing;
      }

      existing?.Dispose();
      var cookies = new CookieContainer();
      var handler = handlerFactory?.Invoke(profile) ?? CreateHandler(profile, cookies);
      if (handler is HttpClientHandler httpHandler && handlerFactory is not null)
      {
        httpHandler.UseCookies = true;
        httpHandler.CookieContainer = cookies;
      }

      var http = new HttpClient(handler, disposeHandler: true)
      {
        BaseAddress = profile.BaseUri,
        Timeout = timeout ?? TimeSpan.FromSeconds(8),
      };
      var created = new SiteClientContext(profile, http, cookies);
      clients[profile.ProfileId] = created;
      return created;
    }
  }

  public void Remove(Guid profileId)
  {
    lock (sync)
    {
      if (clients.Remove(profileId, out var client))
      {
        client.Dispose();
      }
    }
  }

  public void Dispose()
  {
    lock (sync)
    {
      foreach (var client in clients.Values)
      {
        client.Dispose();
      }

      clients.Clear();
    }
  }

  private static HttpClientHandler CreateHandler(SiteProfile profile, CookieContainer cookies)
  {
    var handler = new HttpClientHandler
    {
      UseCookies = true,
      CookieContainer = cookies,
    };
    if (!string.IsNullOrWhiteSpace(profile.CertificateThumbprint))
    {
      var expected = NormalizeThumbprint(profile.CertificateThumbprint);
      handler.ServerCertificateCustomValidationCallback = (_, certificate, _, errors) =>
          certificate is not null &&
          (errors == SslPolicyErrors.None || errors == SslPolicyErrors.RemoteCertificateChainErrors) &&
          NormalizeThumbprint(certificate.GetCertHashString()) == expected;
    }

    return handler;
  }

  private static string NormalizeThumbprint(string value) =>
      value.Replace(" ", string.Empty, StringComparison.Ordinal).ToUpperInvariant();
}

public sealed class SiteApiClient(HttpClient http, CookieContainer cookies)
{
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
  private string? csrfToken;

  public Uri BaseAddress => http.BaseAddress ?? throw new InvalidOperationException("Site BaseAddress is missing.");
  public string? CsrfToken => csrfToken;

  public async Task<SiteHealth> GetHealthAsync(CancellationToken cancellationToken)
  {
    using var response = await http.GetAsync("health", cancellationToken);
    EnsureSiteSuccess(response, "health check");
    var health = await ReadAsync<SiteHealth>(response, cancellationToken);
    if (!string.Equals(health.Service, "upm-site", StringComparison.Ordinal) ||
        !string.Equals(health.Status, "foundation-ready", StringComparison.Ordinal))
    {
      throw new SiteEndpointException(
          "The endpoint responded but did not identify itself as UPM Site.",
          SiteConnectionState.Error);
    }

    return health;
  }

  public async Task<LoginResult> LoginAsync(
      string username,
      string password,
      CancellationToken cancellationToken)
  {
    using var response = await http.PostAsJsonAsync(
        "api/v1/auth/login",
        new { username, password },
        JsonOptions,
        cancellationToken);
    if (response.StatusCode == HttpStatusCode.Unauthorized)
    {
      throw new SiteEndpointException("The username or password was rejected.", SiteConnectionState.AuthenticationRequired);
    }

    EnsureSiteSuccess(response, "login");
    var login = await ReadAsync<LoginResult>(response, cancellationToken);
    csrfToken = login.CsrfToken;
    return login;
  }

  public async Task<AuthSession?> RestoreSessionAsync(CancellationToken cancellationToken)
  {
    using var response = await http.GetAsync("api/v1/auth/session", cancellationToken);
    if (response.StatusCode == HttpStatusCode.Unauthorized)
    {
      csrfToken = null;
      return null;
    }

    EnsureSiteSuccess(response, "session restoration");
    var session = await ReadAsync<AuthSession>(response, cancellationToken);
    csrfToken = session.CsrfToken;
    return session;
  }

  public void RestoreSessionCookie(string token)
  {
    cookies.Add(BaseAddress, new Cookie("upm_site_session", token, "/")
    {
      HttpOnly = true,
      Secure = BaseAddress.Scheme == Uri.UriSchemeHttps,
    });
  }

  public string? GetSessionCookie() =>
      cookies.GetCookies(BaseAddress)["upm_site_session"]?.Value;

  public async Task LogoutAsync(CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, "api/v1/auth/logout");
    using var response = await http.SendAsync(request, cancellationToken);
    if (response.StatusCode is not HttpStatusCode.Unauthorized)
    {
      EnsureSiteSuccess(response, "logout");
    }

    ClearSession();
  }

  public void ClearSession()
  {
    csrfToken = null;
    var cookie = cookies.GetCookies(BaseAddress)["upm_site_session"];
    if (cookie is not null)
    {
      cookie.Expired = true;
    }
  }

  public Task<SiteRegistration> GetRegistrationAsync(CancellationToken cancellationToken) =>
      GetAsync<SiteRegistration>("api/v1/central-registration", cancellationToken);

  public Task<EventDeployment[]> GetEventDeploymentsAsync(CancellationToken cancellationToken) =>
      GetAsync<EventDeployment[]>("api/v1/event-deployments", cancellationToken);

  public async Task<JsonElement> CreateEventAsync(
      string name,
      string timeZone,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, "api/v1/events");
    request.Content = JsonContent.Create(new { name, timezone = timeZone }, options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "local Event creation");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public async Task<JsonElement> UploadProgramImportAsync(
      Guid eventId,
      string filename,
      Stream content,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/events/{eventId}/program-imports");
    using var multipart = new MultipartFormDataContent();
    var stream = new StreamContent(content);
    multipart.Add(stream, "file", filename);
    request.Content = multipart;
    using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
    EnsureSiteSuccess(response, "program import staging");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public async Task<JsonElement> CommitProgramImportAsync(
      Guid batchId,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/program-imports/{batchId}/commit");
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "program import commit");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public Task<JsonElement> GetProgramImportAsync(Guid batchId, CancellationToken cancellationToken) =>
      GetAsync<JsonElement>($"api/v1/program-imports/{batchId}", cancellationToken);

  public async Task<JsonElement> UpdateProgramImportRowAsync(
      Guid batchId,
      Guid rowId,
      IReadOnlyDictionary<string, object?> correctedValues,
      bool reject,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(
        HttpMethod.Patch,
        $"api/v1/program-imports/{batchId}/rows/{rowId}");
    request.Content = JsonContent.Create(
        new { corrected_values = correctedValues, reject },
        options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, reject ? "program row rejection" : "program row correction");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public Task<JsonElement> GetMediaStorageAsync(CancellationToken cancellationToken) =>
      GetAsync<JsonElement>("api/v1/media-storage", cancellationToken);

  public Task<JsonElement[]> GetDevicesAsync(CancellationToken cancellationToken) =>
      GetAsync<JsonElement[]>("api/v1/devices", cancellationToken);

  public Task<JsonElement> GetDeviceRuntimeAsync(Guid deviceId, CancellationToken cancellationToken) =>
      GetAsync<JsonElement>($"api/v1/devices/{deviceId}/runtime", cancellationToken);

  public Task<JsonElement[]> GetRoomsAsync(CancellationToken cancellationToken) =>
      GetAsync<JsonElement[]>("api/v1/rooms", cancellationToken);

  public Task<JsonElement> GetRoomAsync(Guid roomId, CancellationToken cancellationToken) =>
      GetAsync<JsonElement>($"api/v1/rooms/{roomId}", cancellationToken);

  public Task<JsonElement> GetEffectiveRotationAsync(
      Guid eventId,
      DateOnly eventDay,
      Guid? roomId,
      Guid? sessionId,
      CancellationToken cancellationToken)
  {
    var query = $"event_day={eventDay:yyyy-MM-dd}";
    if (roomId.HasValue) query += $"&room_id={roomId.Value}";
    if (sessionId.HasValue) query += $"&session_id={sessionId.Value}";
    return GetAsync<JsonElement>($"api/v1/events/{eventId}/rotating-slides?{query}", cancellationToken);
  }

  public Task<JsonElement> GetEventProgramAsync(Guid eventId, CancellationToken cancellationToken) =>
      GetAsync<JsonElement>($"api/v1/events/{eventId}/program", cancellationToken);

  public Task<JsonElement> GetMediaIntakeAsync(Guid eventId, CancellationToken cancellationToken) =>
      GetAsync<JsonElement>($"api/v1/events/{eventId}/media/intake?limit=100", cancellationToken);

  public Task<JsonElement> FindPresentationEntriesAsync(
      Guid eventId,
      string search,
      CancellationToken cancellationToken) =>
      GetAsync<JsonElement>(
          $"api/v1/events/{eventId}/presentation-lookup?search={Uri.EscapeDataString(search)}&limit=50",
          cancellationToken);

  public async Task<JsonElement> ConfirmMediaAssignmentAsync(
      Guid mediaId,
      Guid presentationId,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/media/{mediaId}/confirmation");
    request.Content = JsonContent.Create(new { presentation_id = presentationId }, options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "media assignment confirmation");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public async Task<JsonElement> ChangeMediaAssignmentAsync(
      Guid mediaId,
      Guid presentationId,
      string idempotencyKey,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/media/{mediaId}/reassignment");
    request.Content = JsonContent.Create(
        new { presentation_id = presentationId, idempotency_key = idempotencyKey },
        options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "media assignment change");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public async Task<JsonElement> CreatePresentationEntryAsync(
      Guid eventId,
      Guid? sessionId,
      string title,
      IReadOnlyList<Guid> presenterIds,
      Guid? mediaObjectId,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/events/{eventId}/presentations");
    request.Content = JsonContent.Create(
        new
        {
          session_id = sessionId,
          title,
          presenter_ids = presenterIds,
          media_object_id = mediaObjectId,
        },
        options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "Presentation Entry creation");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public async Task RejectMediaIntakeAsync(
      Guid mediaId,
      string reason,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/media/{mediaId}/rejection");
    request.Content = JsonContent.Create(new { reason }, options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "intake rejection");
  }

  public async Task RetryMediaCommitAsync(Guid mediaId, CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, $"api/v1/media/{mediaId}/commit-retry");
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "intake commit retry");
  }

  public async Task<IReadOnlyList<JsonElement>> GetPresentationOperationsAsync(
      Guid eventId,
      string? search,
      CancellationToken cancellationToken)
  {
    const int pageSize = 100;
    var result = new List<JsonElement>();
    for (var offset = 0; ; offset += pageSize)
    {
      var searchQuery = string.IsNullOrWhiteSpace(search)
          ? string.Empty
          : $"&search={Uri.EscapeDataString(search.Trim())}";
      var page = await GetAsync<JsonElement>(
          $"api/v1/events/{eventId}/presentations/operations?limit={pageSize}&offset={offset}{searchQuery}",
          cancellationToken);
      result.AddRange(page.Items());
      if (result.Count >= page.Number("total") || page.Items().Count < pageSize)
      {
        return result;
      }
    }
  }

  public Task<JsonElement> GetOperationalLogsAsync(
      Guid? eventId,
      CancellationToken cancellationToken)
  {
    var eventQuery = eventId.HasValue ? $"&event_id={eventId.Value}" : string.Empty;
    return GetAsync<JsonElement>($"api/v1/logs?minutes=1440&limit=250{eventQuery}", cancellationToken);
  }

  public async Task<JsonElement> GetReviewSessionsAsync(CancellationToken cancellationToken)
  {
    using var response = await http.GetAsync(
        "api/v1/review-sessions?limit=200",
        HttpCompletionOption.ResponseHeadersRead,
        cancellationToken);
    if (response.StatusCode == HttpStatusCode.MethodNotAllowed)
    {
      throw new SiteEndpointException(
          "Review listing requires a newer UPM Site build.",
          SiteConnectionState.Error);
    }

    EnsureSiteSuccess(response, "review-session listing");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  public Task<JsonElement> GetOperationsDashboardAsync(CancellationToken cancellationToken) =>
      GetAsync<JsonElement>("api/v1/operations/dashboard", cancellationToken);

  public async Task<HttpResponseMessage> UploadAsync(
      TransferItem item,
      Guid canonicalSiteId,
      Stream body,
      CancellationToken cancellationToken)
  {
    var query = new List<string>
        {
            $"site_id={Uri.EscapeDataString(canonicalSiteId.ToString())}",
            "category=presentation",
            $"expected_size={item.Length}",
        };
    if (item.EventId.HasValue)
    {
      query.Add($"event_id={Uri.EscapeDataString(item.EventId.Value.ToString())}");
    }

    var path = $"api/v1/media/ingestions?{string.Join('&', query)}";
    var response = await SendUploadAsync(path, item, body, cancellationToken);
    if (response.StatusCode != HttpStatusCode.Forbidden || !body.CanSeek)
    {
      return response;
    }

    response.Dispose();
    var session = await RestoreSessionAsync(cancellationToken);
    if (session is null)
    {
      return new HttpResponseMessage(HttpStatusCode.Unauthorized);
    }

    body.Position = 0;
    return await SendUploadAsync(path, item, body, cancellationToken);
  }

  public async Task<JsonDocument> CreateCommandAsync(
      DeviceCommandRequest command,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(HttpMethod.Post, "api/v1/device-commands");
    request.Content = JsonContent.Create(command, options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "device command creation");
    return await JsonDocument.ParseAsync(
        await response.Content.ReadAsStreamAsync(cancellationToken),
        cancellationToken: cancellationToken);
  }

  public async Task CopyPresentationVersionAsync(
      Guid presentationVersionId,
      Stream destination,
      CancellationToken cancellationToken)
  {
    using var response = await http.GetAsync(
        $"api/v1/presentation-versions/{presentationVersionId}/download",
        HttpCompletionOption.ResponseHeadersRead,
        cancellationToken);
    EnsureSiteSuccess(response, "presentation download");
    await using var source = await response.Content.ReadAsStreamAsync(cancellationToken);
    await source.CopyToAsync(destination, cancellationToken);
  }

  public async Task<JsonElement> UpdatePresentationAssignmentAsync(
      Guid presentationId,
      Guid sessionId,
      int revision,
      CancellationToken cancellationToken)
  {
    using var request = CreateWriteRequest(
        HttpMethod.Patch,
        $"api/v1/presentations/{presentationId}/assignment");
    request.Content = JsonContent.Create(
        new { session_id = sessionId, expected_revision = revision },
        options: JsonOptions);
    using var response = await http.SendAsync(request, cancellationToken);
    EnsureSiteSuccess(response, "presentation reassignment");
    return await ReadAsync<JsonElement>(response, cancellationToken);
  }

  private async Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
  {
    using var response = await http.GetAsync(path, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
    EnsureSiteSuccess(response, path);
    return await ReadAsync<T>(response, cancellationToken);
  }

  private static void EnsureSiteSuccess(HttpResponseMessage response, string operation)
  {
    if (response.IsSuccessStatusCode)
    {
      return;
    }

    throw response.StatusCode switch
    {
      HttpStatusCode.Unauthorized => new SiteEndpointException(
          "Site authentication is required or the session expired.",
          SiteConnectionState.SessionExpired),
      HttpStatusCode.Forbidden => new SiteEndpointException(
          $"The logged-in Site user is not permitted to perform {operation}.",
          SiteConnectionState.Error),
      _ when (int)response.StatusCode >= 500 => new SiteEndpointException(
          $"UPM Site could not complete {operation} ({(int)response.StatusCode}).",
          SiteConnectionState.Error),
      _ => new SiteEndpointException(
          $"UPM Site rejected {operation} ({(int)response.StatusCode}).",
          SiteConnectionState.Error),
    };
  }

  private async Task<HttpResponseMessage> SendUploadAsync(
      string path,
      TransferItem item,
      Stream body,
      CancellationToken cancellationToken)
  {
    var request = CreateWriteRequest(HttpMethod.Post, path);
    request.Content = new StreamContent(body);
    request.Headers.Add("Idempotency-Key", item.IdempotencyKey);
    request.Headers.Add("X-UPM-Original-Filename", Uri.EscapeDataString(item.OriginalFilename));
    request.Headers.Add("X-UPM-Source-Relative-Path", Uri.EscapeDataString(item.RelativePath));
    return await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
  }

  private HttpRequestMessage CreateWriteRequest(HttpMethod method, string path)
  {
    var request = new HttpRequestMessage(method, new Uri(path, UriKind.Relative));
    if (!string.IsNullOrWhiteSpace(csrfToken))
    {
      request.Headers.Add("X-CSRF-Token", csrfToken);
    }

    return request;
  }

  private static async Task<T> ReadAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken) =>
      await response.Content.ReadFromJsonAsync<T>(JsonOptions, cancellationToken)
      ?? throw new InvalidDataException($"Site returned an empty {typeof(T).Name} response.");
}
