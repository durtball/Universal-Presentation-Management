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
app.MapPost("/api/v1/presentations/{versionId:guid}/launch", async (Guid versionId, PresentationLauncher launcher, CancellationToken ct) =>
{
  await launcher.LaunchAsync(versionId, ct); return Results.Accepted();
});
app.MapPost("/api/v1/sessions/{sessionId:guid}/intake", async (Guid sessionId, Guid? presentationId, Guid? baseVersionId, IFormFile file, LocalIntakeService intake, CancellationToken ct) =>
{
  var temporary = Path.Combine(Path.GetTempPath(), $"upm-{Guid.NewGuid():N}-{WindowsPathPolicy.UploadedFilename(file.FileName)}");
  try
  {
    await using (var output = File.Create(temporary)) await file.CopyToAsync(output, ct);
    var change = await intake.IngestAsync(sessionId, presentationId, baseVersionId, temporary, ct);
    return Results.Accepted(value: change);
  }
  finally { if (File.Exists(temporary)) File.Delete(temporary); }
}).DisableAntiforgery();
await app.RunAsync();
