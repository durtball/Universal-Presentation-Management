using System.Net;
using UPM.Windows.Agent;

var builder = WebApplication.CreateBuilder(args);
builder.Host.UseWindowsService(options => options.ServiceName = "UPM Room Agent");
builder.WebHost.ConfigureKestrel(options => options.Listen(IPAddress.Loopback, 43821));

var storage = AgentStorage.Default;
storage.EnsureCreated();
builder.Services.AddSingleton(storage);
builder.Services.AddSingleton(new AgentStateStore(storage.DatabasePath));
builder.Services.AddSingleton<PresentationLauncher>();
builder.Services.AddSingleton<IProcessLauncher, ShellProcessLauncher>();
builder.Services.AddSingleton<LocalIntakeService>();
builder.Services.AddSingleton<AgentDashboardService>();
builder.Services.AddSingleton<IAgentCredentialStore, AgentCredentialStore>();
builder.Services.AddSingleton<AgentSyncSignal>();
builder.Services.AddHttpClient<SiteAgentClient>(client => client.Timeout = TimeSpan.FromMinutes(30));
builder.Services.AddSingleton<AgentSyncWorker>();
builder.Services.AddHostedService(provider => provider.GetRequiredService<AgentSyncWorker>());

var app = builder.Build();
var state = app.Services.GetRequiredService<AgentStateStore>();
await state.InitializeAsync();

app.Use(async (context, next) =>
{
  if (!IPAddress.IsLoopback(context.Connection.RemoteIpAddress ?? IPAddress.None))
  {
    context.Response.StatusCode = StatusCodes.Status403Forbidden;
    return;
  }
  await next();
});

app.MapGet("/api/v1/status", async (AgentStateStore store, CancellationToken ct) =>
{
  var provisioning = await store.GetProvisioningAsync(ct);
  var sessions = await store.ListSessionsAsync(ct);
  return Results.Ok(new
  {
    product = "UPM Room Agent",
    provisioned = provisioning is not null,
    device_name = provisioning?.DeviceName,
    role = provisioning?.Role.ToString(),
    room = provisioning?.RoomName,
    last_site_sync = await store.GetLastSuccessfulSyncAsync(ct),
    cached_sessions = sessions.Count,
  });
});
app.MapGet("/api/v1/sessions", (AgentStateStore store, CancellationToken ct) => store.ListSessionsAsync(ct));
app.MapGet("/api/v1/dashboard", (AgentDashboardService dashboard, CancellationToken ct) => dashboard.GetAsync(ct: ct));
app.MapGet("/api/v1/settings", async (AgentStateStore store, AgentDashboardService dashboard, CancellationToken ct) =>
    (await dashboard.GetAsync(ct: ct)).Settings);
app.MapPut("/api/v1/settings", async (AgentSettings settings, AgentStateStore store, CancellationToken ct) =>
{ await store.SaveSettingsAsync(settings, ct); return Results.NoContent(); });
app.MapPost("/api/v1/provisioning", async (ProvisioningRequest request, AgentSyncWorker sync, CancellationToken ct) =>
{ await sync.ProvisionAsync(request, ct); return Results.NoContent(); });
app.MapDelete("/api/v1/provisioning", async (AgentStateStore store, IAgentCredentialStore credentials, CancellationToken ct) =>
{ await credentials.ClearAsync(ct); await store.ClearProvisioningAsync(ct); return Results.NoContent(); });
app.MapPost("/api/v1/sync", (AgentSyncSignal signal) => { signal.Request(); return Results.Accepted(); });
app.MapGet("/api/v1/presentation-library", async (AgentDashboardService dashboard, CancellationToken ct) =>
    Results.Ok(new { path = (await dashboard.GetAsync(ct: ct)).Settings.PresentationLibraryPath }));
app.MapPost("/api/v1/presentation-library/rebuild", async (AgentStateStore store, AgentDashboardService dashboard, CancellationToken ct) =>
{
  var stateView = await dashboard.GetAsync(ct: ct); var library = new PresentationLibrary(stateView.Settings.PresentationLibraryPath);
  foreach (var asset in await store.ListAssetsAsync(ct))
  {
    var session = (await store.ListSessionsAsync(ct)).FirstOrDefault(row => row.SessionId == asset.SessionId);
    if (asset.Verified) await library.PublishAsync(asset, session, ct);
  }
  return Results.NoContent();
});
app.MapGet("/api/v1/branding", async (AgentDashboardService dashboard, CancellationToken ct) => (await dashboard.GetAsync(ct: ct)).Branding);
app.MapPost("/api/v1/presentations/{versionId:guid}/launch", async (Guid versionId, PresentationLauncher launcher, CancellationToken ct) =>
{
  await launcher.LaunchAsync(versionId, ct); return Results.Accepted();
});
app.MapPost("/api/v1/sessions/{sessionId:guid}/intake", async (Guid sessionId, Guid? presentationId, Guid? baseVersionId, IFormFile file, LocalIntakeService intake, AgentStateStore store, AgentDashboardService dashboard, CancellationToken ct) =>
{
  var temporary = Path.Combine(Path.GetTempPath(), $"upm-{Guid.NewGuid():N}-{WindowsPathPolicy.UploadedFilename(file.FileName)}");
  try
  {
    await using (var output = File.Create(temporary)) await file.CopyToAsync(output, ct);
    var change = await intake.IngestAsync(sessionId, presentationId, baseVersionId, temporary, ct, file.FileName);
    var asset = await store.GetVerifiedVersionAsync(change.LocalVersionId, ct);
    var session = (await store.ListSessionsAsync(ct)).FirstOrDefault(row => row.SessionId == sessionId);
    var settings = (await dashboard.GetAsync(ct: ct)).Settings;
    if (asset is not null && session is not null && settings.PresentationLibraryEnabled)
    {
      var visible = await new PresentationLibrary(settings.PresentationLibraryPath).PublishAsync(asset, session, ct);
      await store.SetLibraryPathAsync(asset.AssetId, sessionId, visible, ct);
    }
    return Results.Accepted(value: change);
  }
  finally { if (File.Exists(temporary)) File.Delete(temporary); }
}).DisableAntiforgery();
await app.RunAsync();
