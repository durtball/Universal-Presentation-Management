using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace UPM.Signage;
internal sealed record SignageHealth(string Service, string Status, [property: JsonPropertyName("source_connected")] bool SourceConnected);
internal sealed record PairingResult([property: JsonPropertyName("display_id")] Guid DisplayId, [property: JsonPropertyName("player_credential")] string PlayerCredential);
internal sealed class SignageApiClient : IDisposable
{
  private readonly HttpClient http = new() { Timeout = TimeSpan.FromSeconds(15) };
  public Uri ValidateEndpoint(string value)
  {
    if (!Uri.TryCreate(value.TrimEnd('/') + "/", UriKind.Absolute, out var uri) ||
        (uri.Scheme != Uri.UriSchemeHttps && !(uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback)))
      throw new InvalidOperationException("Use HTTPS, or HTTP only for a loopback Signage server.");
    return uri;
  }
  public async Task<SignageHealth> HealthAsync(string endpoint, CancellationToken ct = default)
  {
    var response = await http.GetAsync(new Uri(ValidateEndpoint(endpoint), "health"), ct); response.EnsureSuccessStatusCode();
    return (await response.Content.ReadFromJsonAsync<SignageHealth>(cancellationToken: ct))!;
  }
  public async Task LoginAsync(string endpoint, string password, CancellationToken ct = default)
  {
    using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(ValidateEndpoint(endpoint), "api/v1/layouts"));
    request.Headers.Add("X-UPM-Operator-Password", password);
    using var response = await http.SendAsync(request, ct); response.EnsureSuccessStatusCode();
  }
  public async Task<PairingResult> PairAsync(string endpoint, string password, object body, CancellationToken ct = default)
  {
    using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(ValidateEndpoint(endpoint), "api/v1/displays/pair")) { Content = JsonContent.Create(body) };
    request.Headers.Add("X-UPM-Operator-Password", password);
    using var response = await http.SendAsync(request, ct); response.EnsureSuccessStatusCode();
    return (await response.Content.ReadFromJsonAsync<PairingResult>(cancellationToken: ct))!;
  }
  public async Task<string> ManifestAsync(string endpoint, string credential, CancellationToken ct = default)
  {
    using var request = new HttpRequestMessage(HttpMethod.Get, new Uri(ValidateEndpoint(endpoint), "api/v1/player/manifest"));
    request.Headers.Authorization = new("Bearer", credential);
    using var response = await http.SendAsync(request, ct); response.EnsureSuccessStatusCode();
    return await response.Content.ReadAsStringAsync(ct);
  }
  public void Dispose() => http.Dispose();
}
