using System.Net.Http.Json;
using System.Text.Json;
using UPM.Windows.Core;
namespace UPM.Windows.SiteApi;

public sealed record SiteUser(Guid UserId, string Username, string DisplayName, string[] Roles, string[] Permissions);
public sealed record LoginResult(bool Authenticated, SiteUser User, string CsrfToken);
public sealed record DeviceCommandRequest(Guid DeviceId, Guid? RoomId, string CommandType, Dictionary<string, object> Payload, string IdempotencyKey, DateTimeOffset? ExpiresAt = null, Guid? CorrelationId = null);
public sealed class SiteApiClient(HttpClient http)
{
  string? csrf;
  public async Task<LoginResult> LoginAsync(string username, string password, CancellationToken ct) { using var r = await http.PostAsJsonAsync("api/v1/auth/login", new { username, password }, ct); r.EnsureSuccessStatusCode(); var x = await r.Content.ReadFromJsonAsync<LoginResult>(cancellationToken: ct) ?? throw new InvalidDataException("Empty login response"); csrf = x.CsrfToken; return x; }
  public async Task<HttpResponseMessage> UploadAsync(TransferItem item, Stream body, CancellationToken ct) { var uri = $"api/v1/media/ingestions?site_id={item.SiteProfileId}&category=presentation&expected_size={item.Length}" + (item.EventId is null ? "" : $"&event_id={item.EventId}"); using var q = new HttpRequestMessage(HttpMethod.Post, uri) { Content = new StreamContent(body) }; q.Headers.Add("Idempotency-Key", item.IdempotencyKey); q.Headers.Add("X-UPM-Original-Filename", Uri.EscapeDataString(item.OriginalFilename)); q.Headers.Add("X-UPM-Source-Relative-Path", Uri.EscapeDataString(item.RelativePath)); if (csrf is not null) q.Headers.Add("X-CSRF-Token", csrf); return await http.SendAsync(q, HttpCompletionOption.ResponseHeadersRead, ct); }
  public async Task<JsonDocument> CreateCommandAsync(DeviceCommandRequest command, CancellationToken ct) { using var q = new HttpRequestMessage(HttpMethod.Post, "api/v1/device-commands") { Content = JsonContent.Create(command) }; if (csrf is not null) q.Headers.Add("X-CSRF-Token", csrf); using var r = await http.SendAsync(q, ct); r.EnsureSuccessStatusCode(); return await JsonDocument.ParseAsync(await r.Content.ReadAsStreamAsync(ct), cancellationToken: ct); }
  public Task<HttpResponseMessage> GetAsync(string path, CancellationToken ct) => http.GetAsync(path, HttpCompletionOption.ResponseHeadersRead, ct);
}
