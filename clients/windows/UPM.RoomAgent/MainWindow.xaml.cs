using System.Diagnostics;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media.Imaging;
using UPM.Windows.Agent;
using Windows.Graphics;
using Windows.Storage.Pickers;
using WinRT.Interop;
using H.NotifyIcon;

namespace UPM.RoomAgent;

public sealed partial class MainWindow : Window
{
  private readonly LocalAgentClient agent;
  private readonly DispatcherTimer refresh = new() { Interval = TimeSpan.FromSeconds(5) };
  private AgentDashboard? dashboard;
  private IReadOnlyList<AgentSession> sessions = [];
  private int selectedSession = -1;
  private bool explicitExit;

  public MainWindow(LocalAgentClient agent)
  {
    this.agent = agent; InitializeComponent();
    AppWindow.Resize(new SizeInt32(1440, 900));
    refresh.Tick += async (_, _) => await RefreshAsync();
    Activated += async (_, _) => { if (!refresh.IsEnabled) refresh.Start(); await RefreshAsync(); };
    Closed += (_, _) => refresh.Stop();
    AppWindow.Closing += (_, args) =>
    {
      if (explicitExit) return;
      args.Cancel = true; this.Hide();
    };
    TrayIcon.ForceCreate();
  }

  private async Task RefreshAsync()
  {
    try
    {
      var next = await agent.DashboardAsync(); sessions = await agent.SessionsAsync() ?? [];
      if (next is null) return; dashboard = next; Render(next); AgentStatusText.Text = "CONNECTED"; AgentStatusText.Foreground = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["SuccessBrush"];
      if (next.Role == DeviceRole.UploadKiosk && KioskView.Visibility != Visibility.Visible) ShowKiosk();
    }
    catch
    {
      AgentStatusText.Text = "DISCONNECTED"; SiteStatusText.Text = "UNKNOWN"; OfflineBar.IsOpen = true;
      OfflineBar.Title = "UPM ROOM AGENT STARTING"; OfflineBar.Message = "The embedded Agent runtime is starting. This screen will reconnect automatically.";
    }
  }

  private void Render(AgentDashboard state)
  {
    IdentityText.Text = $"{state.EventName ?? "Not provisioned"}  •  {state.RoomName ?? "No room"}  •  {state.Role}";
    SiteStatusText.Text = state.SiteStatus; OfflineBar.IsOpen = state.ConnectionPhase == AgentConnectionPhase.Offline; OfflineBar.Title = "SITE OFFLINE"; OfflineBar.Message = "Using cached data. Presentations already downloaded remain available.";
    var session = selectedSession >= 0 && selectedSession < sessions.Count ? sessions[selectedSession] : null;
    RenderSession(session is null ? state.CurrentSession : Project(session, state));
    NextTitle.Text = state.NextSession?.Title ?? "None scheduled"; NextTime.Text = state.NextSession is null ? "—" : $"{state.NextSession.StartsAt:g}";
    EventRoomText.Text = $"{state.EventName ?? "No event"}  •  {state.RoomName ?? "No room assignment"}  •  {state.Role}";
    LastSyncText.Text = state.LastSiteSync is null ? "No successful Site sync" : $"Last Site sync {state.LastSiteSync:g}";
    OpenKioskButton.Visibility = state.Role.HasFlag(DeviceRole.UploadKiosk) || state.Settings.KioskEnabled ? Visibility.Visible : Visibility.Collapsed;
    ApplySettings(state); ApplyBranding(state.Branding);
  }

  private static SessionView Project(AgentSession value, AgentDashboard state) => new(value.SessionId, value.SessionIdentifier, value.Title, value.Presenter, value.StartsAt, value.EndsAt,
      state.CurrentSession?.SessionId == value.SessionId ? state.CurrentSession.Presentation : null, state.CurrentSession?.SessionId == value.SessionId ? state.CurrentSession.RotatingSlideSource : "Day / Global");

  private void RenderSession(SessionView? value)
  {
    CurrentTitle.Text = value?.Title ?? "No current session"; CurrentIdentifier.Text = value?.SessionIdentifier is null ? "" : $"SESSION {value.SessionIdentifier}";
    CurrentTime.Text = value is null ? "—" : $"{value.StartsAt:t} – {value.EndsAt:t}"; CurrentPresenter.Text = value?.Presenter ?? "Presenter not listed";
    PresentationTitle.Text = value?.Presentation?.Title ?? "Not available"; OriginalFilename.Text = value?.Presentation?.OriginalFilename ?? "—";
    ReadinessText.Text = (value?.Presentation?.Readiness ?? ReadinessState.NotAvailable).ToString().ToUpperInvariant(); RotationText.Text = $"Rotating slides: {value?.RotatingSlideSource ?? "Day / Global"}";
  }

  private void ApplySettings(AgentDashboard state)
  {
    var value = state.Settings; LibraryEnabled.IsOn = value.PresentationLibraryEnabled; LibraryVisible.IsOn = value.PresentationLibraryVisible; LibraryPath.Text = value.PresentationLibraryPath;
    KeepDays.Value = value.KeepCompletedDays; PreviousDays.Value = value.RetainPreviousVersionsDays; AutomaticDownloads.IsOn = value.AutomaticDownloads; AutomaticActivation.IsOn = value.AutomaticActivation;
    DefaultApplication.Text = value.DefaultPresentationApplication ?? ""; AutoLaunch.IsOn = value.AutoLaunchPresentation; CacheDays.Value = value.CacheRetentionDays; Concurrency.Value = value.TransferConcurrency;
    KioskEnabled.IsOn = value.KioskEnabled; KioskAutoLaunch.IsOn = value.KioskAutoLaunch; KioskFullscreen.IsOn = value.KioskFullscreen; KioskMonitor.Value = value.KioskMonitor; KioskStart.IsOn = value.KioskStartWithWindows; KioskOffline.IsOn = value.KioskOfflineAvailable;
    StartWithWindows.IsOn = value.StartWithWindows;
    SyncSettingsStatus.Text = $"{state.SiteStatus}\nLast sync: {state.LastSiteSync:g}\nFailed transfers: {state.FailedTransfers}"; ProvisioningSummary.Text = $"Site: {state.SiteName ?? "Not provisioned"}\nEvent: {state.EventName ?? "—"}\nRoom: {state.RoomName ?? "—"}\nRole: {state.Role}";
    BrandingSummary.Text = $"Source: {state.Branding.Source}\nRevision: {state.Branding.Revision}\nLogo cached: {File.Exists(state.Branding.EventLogoPath)}\nBackground cached: {File.Exists(state.Branding.KioskBackgroundPath)}";
    DiagnosticsText.Text = $"Agent {state.AgentVersion}\n{state.WindowsVersion}\nFree disk: {state.FreeDiskBytes / 1_073_741_824d:F1} GiB\nCache: {state.CacheBytes / 1_048_576d:F1} MiB\nPowerPoint: {(state.PowerPointDetected ? "Detected" : "Not detected")}\nFailed transfers: {state.FailedTransfers}";
  }

  private void ApplyBranding(BrandingState value)
  {
    KioskEvent.Text = string.IsNullOrWhiteSpace(value.EventName) ? "UPM Upload Kiosk" : value.EventName; KioskWelcome.Text = value.WelcomeMessage ?? "Welcome"; KioskInstructions.Text = value.UploadInstructions ?? "Choose your presentation to upload or replace.";
    KioskLogo.Source = Image(value.KioskLogoPath ?? value.EventLogoPath); KioskBackground.Source = Image(value.KioskBackgroundPath);
  }
  private static BitmapImage? Image(string? path) => File.Exists(path) ? new BitmapImage(new Uri(path)) : null;

  private void NavigationChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
  { var tag = (args.SelectedItem as NavigationViewItem)?.Tag?.ToString(); RoomView.Visibility = tag == "room" ? Visibility.Visible : Visibility.Collapsed; SettingsView.Visibility = tag == "settings" ? Visibility.Visible : Visibility.Collapsed; KioskView.Visibility = tag == "kiosk" ? Visibility.Visible : Visibility.Collapsed; }
  private async void SyncClick(object sender, RoutedEventArgs e) { await RunAsync(async () => { await agent.SyncAsync(); await RefreshAsync(); }, "Synchronization requested."); }
  private async void OpenPresentationClick(object sender, RoutedEventArgs e) { var version = dashboard?.CurrentSession?.Presentation?.VersionId; if (version is null) { await Message("Presentation not ready", "A verified local presentation is not available."); return; } await RunAsync(() => agent.LaunchAsync(version.Value), "Presentation opened."); }
  private async void IntakeClick(object sender, RoutedEventArgs e) => await IntakeAsync(false);
  private async void KioskIntakeClick(object sender, RoutedEventArgs e) => await IntakeAsync(true);
  private async Task IntakeAsync(bool kiosk)
  {
    var session = selectedSession >= 0 && selectedSession < sessions.Count ? sessions[selectedSession].SessionId : dashboard?.CurrentSession?.SessionId;
    if (session is null) { await Message("No session selected", "Select a current or scheduled session before intake."); return; }
    var picker = new FileOpenPicker(); InitializeWithWindow.Initialize(picker, WindowNative.GetWindowHandle(this)); picker.FileTypeFilter.Add(".ppt"); picker.FileTypeFilter.Add(".pptx"); picker.FileTypeFilter.Add(".pps"); picker.FileTypeFilter.Add(".ppsx");
    var file = await picker.PickSingleFileAsync(); if (file is null) return;
    await RunAsync(() => agent.IntakeAsync(session.Value, file.Path), kiosk ? "Presentation staged safely in the local Agent." : "Replacement is ready locally.", kiosk);
  }
  private void PreviousClick(object sender, RoutedEventArgs e) { if (sessions.Count == 0) return; selectedSession = selectedSession <= 0 ? 0 : selectedSession - 1; Render(dashboard!); }
  private void NextClick(object sender, RoutedEventArgs e) { if (sessions.Count == 0) return; selectedSession = Math.Min(sessions.Count - 1, selectedSession + 1); Render(dashboard!); }
  private void OpenKioskClick(object sender, RoutedEventArgs e) => ShowKiosk();
  private void ShowKiosk() { RoomView.Visibility = SettingsView.Visibility = Visibility.Collapsed; KioskView.Visibility = Visibility.Visible; if (dashboard?.Settings.KioskFullscreen == true) AppWindow.SetPresenter(AppWindowPresenterKind.FullScreen); }
  private void ReturnRoomClick(object sender, RoutedEventArgs e) { AppWindow.SetPresenter(AppWindowPresenterKind.Default); KioskView.Visibility = Visibility.Collapsed; RoomView.Visibility = Visibility.Visible; }
  private async void OpenFolderClick(object sender, RoutedEventArgs e) { var path = dashboard?.Settings.PresentationLibraryPath; if (path is null || !Directory.Exists(path)) { await Message("Folder unavailable", "The presentation library has not been built yet."); return; } Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
  private async void RebuildClick(object sender, RoutedEventArgs e) => await RunAsync(agent.RebuildAsync, "Presentation library rebuilt.");
  private async void SaveSettingsClick(object sender, RoutedEventArgs e) { if (dashboard is null) return; var old = dashboard.Settings; var value = old with { PresentationLibraryEnabled = LibraryEnabled.IsOn, PresentationLibraryVisible = LibraryVisible.IsOn, PresentationLibraryPath = LibraryPath.Text, KeepCompletedDays = (int)KeepDays.Value, RetainPreviousVersionsDays = (int)PreviousDays.Value, AutomaticDownloads = AutomaticDownloads.IsOn, AutomaticActivation = AutomaticActivation.IsOn, DefaultPresentationApplication = DefaultApplication.Text, AutoLaunchPresentation = AutoLaunch.IsOn, CacheRetentionDays = (int)CacheDays.Value, TransferConcurrency = (int)Concurrency.Value, KioskEnabled = KioskEnabled.IsOn, KioskAutoLaunch = KioskAutoLaunch.IsOn, KioskFullscreen = KioskFullscreen.IsOn, KioskMonitor = (int)KioskMonitor.Value, KioskStartWithWindows = KioskStart.IsOn, KioskOfflineAvailable = KioskOffline.IsOn, StartWithWindows = StartWithWindows.IsOn }; WindowsStartupService.SetEnabled(value.StartWithWindows); await RunAsync(() => agent.SaveSettingsAsync(value), "Settings saved."); }
  private async void ProvisionClick(object sender, RoutedEventArgs e)
  {
    var site = new TextBox { Header = "Site address", PlaceholderText = "https://site.example" }; var device = new TextBox { Header = "Device UUID" }; var credential = new PasswordBox { Header = "One-time Agent enrollment credential" }; var name = new TextBox { Header = "Device name", Text = Environment.MachineName };
    var panel = new StackPanel { Spacing = 8 }; panel.Children.Add(site); panel.Children.Add(device); panel.Children.Add(credential); panel.Children.Add(name);
    var dialog = new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Provision UPM Room Agent", Content = panel, PrimaryButtonText = "Provision", CloseButtonText = "Cancel" };
    if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
    if (!Uri.TryCreate(site.Text, UriKind.Absolute, out var uri) || !Guid.TryParse(device.Text, out var id)) { await Message("Invalid provisioning", "Enter a valid Site address and device UUID."); return; }
    await RunAsync(() => agent.ProvisionAsync(new(uri, id, credential.Password, name.Text)), "Agent provisioned and synchronization started.");
  }
  private async void UnprovisionClick(object sender, RoutedEventArgs e) { var dialog = new ContentDialog { XamlRoot = Content.XamlRoot, Title = "Unprovision this Agent?", Content = "Cached presentations remain, but Site identity and credentials will be removed.", PrimaryButtonText = "Unprovision", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Close }; if (await dialog.ShowAsync() == ContentDialogResult.Primary) await RunAsync(agent.UnprovisionAsync, "Agent unprovisioned."); }
  private async void ResetDiscoveryClick(object sender, RoutedEventArgs e) => await RunAsync(agent.ResetDiscoveryAsync, "Site discovery restarted.");
  private void TrayOpenClick(object sender, RoutedEventArgs e) { this.Show(); Activate(); }
  private void TrayKioskClick(object sender, RoutedEventArgs e) { this.Show(); Activate(); ShowKiosk(); }
  private async void TrayStatusClick(object sender, RoutedEventArgs e) => await Message("UPM Room Agent Status", dashboard?.SiteStatus ?? "Starting UPM Room Agent");
  private async void TrayExitClick(object sender, RoutedEventArgs e) { explicitExit = true; TrayIcon.Dispose(); refresh.Stop(); await App.Current.ExitAgentAsync(); }
  private async void ReportProblemClick(object sender, RoutedEventArgs e) => await ExportDiagnostics();
  private async void ExportDiagnosticsClick(object sender, RoutedEventArgs e) => await ExportDiagnostics();
  private async Task ExportDiagnostics() { var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), $"UPM-Diagnostics-{DateTime.Now:yyyyMMdd-HHmmss}.txt"); await File.WriteAllTextAsync(path, DiagnosticsText.Text); await Message("Diagnostics exported", path); }
  private void OpenLogsClick(object sender, RoutedEventArgs e) { var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "UPM", "Agent", "logs"); Directory.CreateDirectory(path); Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
  private async Task RunAsync(Func<Task> action, string success, bool kiosk = false) { try { await action(); if (kiosk) { KioskFeedback.Severity = InfoBarSeverity.Success; KioskFeedback.Message = success; KioskFeedback.IsOpen = true; } else await Message("UPM Room Agent", success); } catch (Exception ex) { if (kiosk) { KioskFeedback.Severity = InfoBarSeverity.Error; KioskFeedback.Message = ex.Message; KioskFeedback.IsOpen = true; } else await Message("Action failed", ex.Message); } }
  private async Task Message(string title, string content) { var dialog = new ContentDialog { XamlRoot = Content.XamlRoot, Title = title, Content = content, CloseButtonText = "OK" }; await dialog.ShowAsync(); }
}
