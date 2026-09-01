using System.Reflection;
using Microsoft.Win32;

namespace UPM.Windows.Agent;

public sealed class AgentDashboardService(AgentStateStore state, AgentStorage storage)
{
  public async Task<AgentDashboard> GetAsync(DateTimeOffset? selectedAt = null, CancellationToken ct = default)
  {
    var provisioning = await state.GetProvisioningAsync(ct);
    var sessions = await state.ListSessionsAsync(ct);
    var assets = await state.ListAssetsAsync(ct);
    var now = selectedAt ?? DateTimeOffset.Now;
    var current = sessions.FirstOrDefault(row => !row.Cancelled && row.StartsAt <= now && row.EndsAt > now)
        ?? sessions.FirstOrDefault(row => !row.Cancelled && row.StartsAt >= now);
    var next = current is null ? null : sessions.FirstOrDefault(row => !row.Cancelled && row.StartsAt > current.StartsAt);
    var branding = await state.GetBrandingAsync(ct) ?? new(0, "Site Managed", string.Empty, null, null, null, null, null, null, null, null, null, null, null, null);
    var library = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "UPM Presentations");
    var settings = await state.GetSettingsAsync(ct) ?? AgentSettings.Default(library);
    var drive = new DriveInfo(Path.GetPathRoot(storage.Root)!);
    var connected = await state.GetSiteConnectedAsync(ct); var phase = await state.GetConnectionPhaseAsync(ct);
    if (!connected && provisioning is not null && phase == AgentConnectionPhase.Starting)
      phase = AgentConnectionPhase.Offline;
    var status = phase switch
    {
      AgentConnectionPhase.Starting => "STARTING UPM ROOM AGENT",
      AgentConnectionPhase.Discovering => "DISCOVERING UPM SITE…",
      AgentConnectionPhase.SiteFound => "UPM SITE FOUND",
      AgentConnectionPhase.Registering => "REGISTERING THIS COMPUTER…",
      AgentConnectionPhase.WaitingForAssignment => "CONNECTED TO UPM SITE — Waiting for room assignment",
      AgentConnectionPhase.Synchronizing => "CONNECTED — Synchronizing…",
      AgentConnectionPhase.Connected => "CONNECTED",
      _ => "OFFLINE — Using cached Site configuration",
    };
    var identity = await state.GetOrCreateIdentityAsync(ct);
    return new(true, connected, status,
        provisioning?.SiteName, provisioning?.EventName, provisioning?.RoomName, provisioning?.Role ?? DeviceRole.None,
        Project(current, assets), Project(next, assets), await state.GetLastSuccessfulSyncAsync(ct), branding, settings,
        Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "0.0.0", Environment.OSVersion.VersionString,
        drive.AvailableFreeSpace, DirectorySize(storage.Cache), assets.Count(row => !row.Verified), DetectPowerPoint(),
        phase, identity.AgentId, provisioning?.SiteId, await state.GetPresentationLibraryErrorAsync(ct));
  }

  private static SessionView? Project(AgentSession? session, IReadOnlyList<AgentAsset> assets)
  {
    if (session is null) return null;
    var asset = assets.FirstOrDefault(row => row.SessionId == session.SessionId && row.Kind == AssetKind.Presentation && row.Verified);
    var presentation = asset is null ? null : new PresentationView(
        asset.PresentationId ?? asset.AssetId, asset.VersionId, session.Title, asset.OriginalFilename,
        asset.Verified ? ReadinessState.Ready : ReadinessState.Waiting, asset.Verified ? 100 : 0, null);
    var rotation = assets.Any(row => row.SessionId == session.SessionId && row.Kind == AssetKind.RotatingSlide) ? "Session Override"
        : assets.Any(row => row.RoomId == session.RoomId && row.Kind == AssetKind.RotatingSlide) ? "Room Override" : "Day / Global";
    return new(session.SessionId, session.SessionIdentifier, session.Title, session.Presenter, session.StartsAt, session.EndsAt, presentation, rotation);
  }

  private static long DirectorySize(string path) => Directory.Exists(path)
      ? Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories).Sum(file => new FileInfo(file).Length) : 0;
  private static bool DetectPowerPoint() => OperatingSystem.IsWindows() &&
      Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\POWERPNT.EXE") is not null;
}
