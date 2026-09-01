using System.Net.Http.Json;
using UPM.Windows.Agent;

namespace UPM.RoomAgent;

public sealed class LocalAgentClient
{
  private readonly HttpClient http = new() { BaseAddress = new Uri("http://127.0.0.1:43821"), Timeout = TimeSpan.FromSeconds(10) };
  public Task<AgentDashboard?> DashboardAsync(CancellationToken ct = default) => http.GetFromJsonAsync<AgentDashboard>("api/v1/dashboard", ct);
  public Task<IReadOnlyList<AgentSession>?> SessionsAsync(CancellationToken ct = default) => http.GetFromJsonAsync<IReadOnlyList<AgentSession>>("api/v1/sessions", ct);
  public async Task SyncAsync(CancellationToken ct = default) { using var response = await http.PostAsync("api/v1/sync", null, ct); response.EnsureSuccessStatusCode(); }
  public async Task ResetDiscoveryAsync(CancellationToken ct = default) { using var response = await http.PostAsync("api/v1/discovery/reset", null, ct); response.EnsureSuccessStatusCode(); }
  public async Task LaunchAsync(Guid version, CancellationToken ct = default) { using var response = await http.PostAsync($"api/v1/presentations/{version}/launch", null, ct); response.EnsureSuccessStatusCode(); }
  public async Task ProvisionAsync(ProvisioningRequest request, CancellationToken ct = default) { using var response = await http.PostAsJsonAsync("api/v1/provisioning", request, ct); response.EnsureSuccessStatusCode(); }
  public async Task UnprovisionAsync(CancellationToken ct = default) { using var response = await http.DeleteAsync("api/v1/provisioning", ct); response.EnsureSuccessStatusCode(); }
  public async Task SaveSettingsAsync(AgentSettings settings, CancellationToken ct = default) { using var response = await http.PutAsJsonAsync("api/v1/settings", settings, ct); response.EnsureSuccessStatusCode(); }
  public async Task RebuildAsync(CancellationToken ct = default) { using var response = await http.PostAsync("api/v1/presentation-library/rebuild", null, ct); response.EnsureSuccessStatusCode(); }
  public async Task IntakeAsync(Guid session, string path, CancellationToken ct = default)
  { await using var stream = File.OpenRead(path); using var form = new MultipartFormDataContent(); form.Add(new StreamContent(stream), "file", Path.GetFileName(path)); using var response = await http.PostAsync($"api/v1/sessions/{session}/intake", form, ct); response.EnsureSuccessStatusCode(); }
}
