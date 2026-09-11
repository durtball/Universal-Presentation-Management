using System.Text.Json;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.Graphics;

namespace UPM.Signage;

public sealed partial class MainWindow : Window
{
  private readonly SignageApiClient api = new();
  private readonly SignageConfigurationStore store = new();
  private SignageConfiguration configuration = new();
  private string? operatorPassword;
  private int rendererRecoveries;
  private DateTimeOffset recoveryWindow = DateTimeOffset.UtcNow;
  private IReadOnlyList<MonitorInfo> monitors = [];

  public MainWindow()
  {
    InitializeComponent();
    Closed += (_, _) => api.Dispose();
    _ = InitializeAsync();
  }

  private async Task InitializeAsync()
  {
    configuration = await store.LoadAsync();
    ServerUrl.Text = configuration.ServerUrl; DisplayName.Text = configuration.DisplayName;
    Fullscreen.IsOn = configuration.Fullscreen; PlayerWidth.Value = configuration.Width; PlayerHeight.Value = configuration.Height; StartWithWindows.IsOn = configuration.StartWithWindows;
    monitors = MonitorService.FindAll();
    foreach (var display in monitors) Monitor.Items.Add(display.Name);
    Monitor.SelectedIndex = Math.Clamp(configuration.Monitor, 0, Monitor.Items.Count - 1);
    try { _ = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
    catch (Exception exception) { Diagnostics.Text = "Renderer unavailable: install the Microsoft Edge WebView2 Runtime. " + exception.Message; return; }
    await Player.EnsureCoreWebView2Async();
    var assets = Path.Combine(AppContext.BaseDirectory, "Assets", "player");
    Player.CoreWebView2.SetVirtualHostNameToFolderMapping("player.upm.local", assets, CoreWebView2HostResourceAccessKind.DenyCors);
    Player.Source = new Uri("https://player.upm.local/index.html");
    if (Environment.GetCommandLineArgs().Contains("--player", StringComparer.OrdinalIgnoreCase)) PlayerClick(this, new RoutedEventArgs());
  }

  private async void TestClick(object sender, RoutedEventArgs e)
  {
    try { var health = await api.HealthAsync(ServerUrl.Text); ServiceStatus.Text = health.SourceConnected ? "Signage ready · Site source connected" : "Signage ready · Site source offline (cached schedule active)"; }
    catch (Exception exception) { ServiceStatus.Text = "Signage server unavailable"; OperatorMessage.Text = exception.Message; }
  }

  private async void LoginClick(object sender, RoutedEventArgs e)
  {
    try
    {
      await api.LoginAsync(ServerUrl.Text, OperatorPassword.Password); operatorPassword = OperatorPassword.Password; OperatorPassword.Password = "";
      Designer.Source = new Uri(api.ValidateEndpoint(ServerUrl.Text), ""); Designer.Visibility = Visibility.Visible; DesignerUnavailable.Visibility = Visibility.Collapsed;
      OperatorMessage.Text = "Authenticated. Designer changes are saved by the Signage API.";
    }
    catch (Exception exception) { operatorPassword = null; OperatorMessage.Text = "Login failed: " + exception.Message; }
  }

  private async void PairClick(object sender, RoutedEventArgs e)
  {
    if (operatorPassword is null) { OperatorMessage.Text = "Log in before pairing a display."; return; }
    try
    {
      var roomDoor = ((ComboBoxItem)DisplayMode.SelectedItem).Content.ToString() == "room_door";
      Guid? eventId = roomDoor ? Guid.Parse(EventId.Text) : null; Guid? roomId = roomDoor ? Guid.Parse(RoomId.Text) : null;
      var result = await api.PairAsync(ServerUrl.Text, operatorPassword, new { name = DisplayName.Text, aspect_ratio = ((ComboBoxItem)AspectRatio.SelectedItem).Content, mode = ((ComboBoxItem)DisplayMode.SelectedItem).Content, event_id = eventId, room_id = roomId });
      DisplayCredentialStore.Save(result.DisplayId, result.PlayerCredential);
      configuration = configuration with { ServerUrl = ServerUrl.Text.TrimEnd('/'), DisplayId = result.DisplayId, DisplayName = DisplayName.Text };
      await store.SaveAsync(configuration); OperatorMessage.Text = $"Display {result.DisplayId} paired and assigned. Its credential is protected for this Windows user.";
    }
    catch (Exception exception) { OperatorMessage.Text = "Pairing failed: " + exception.Message; }
  }

  private async void DesignerNavigationCompleted(WebView2 sender, CoreWebView2NavigationCompletedEventArgs args)
  {
    if (args.IsSuccess && operatorPassword is not null)
      await Designer.ExecuteScriptAsync($"window.upmSetOperatorPassword({JsonSerializer.Serialize(operatorPassword)})");
  }

  private void OperatorClick(object sender, RoutedEventArgs e) { OperatorPanel.Visibility = Visibility.Visible; PlayerPanel.Visibility = Visibility.Collapsed; }
  private async void PlayerClick(object sender, RoutedEventArgs e) { OperatorPanel.Visibility = Visibility.Collapsed; PlayerPanel.Visibility = Visibility.Visible; await RefreshPlayerAsync(); }

  private async void PlayerMessageReceived(WebView2 sender, CoreWebView2WebMessageReceivedEventArgs args) => await RefreshPlayerAsync();
  private async Task RefreshPlayerAsync()
  {
    var credential = DisplayCredentialStore.Read(configuration.DisplayId);
    if (credential is null) { Diagnostics.Text = "No paired display credential. Pair this player in Operator mode."; await ShowCachedAsync(); return; }
    try
    {
      var manifest = await api.ManifestAsync(configuration.ServerUrl, credential); await SignageConfigurationStore.SaveManifestAsync(manifest);
      await Player.ExecuteScriptAsync($"window.upmRenderManifest({manifest})"); Diagnostics.Text = "Renderer healthy · Signage server connected";
    }
    catch (HttpRequestException) { Diagnostics.Text = "Signage server unavailable · showing last verified screen only"; await ShowCachedAsync(); }
    catch (TaskCanceledException) { Diagnostics.Text = "Signage server timed out · showing last verified screen only"; await ShowCachedAsync(); }
    catch (Exception exception) { Diagnostics.Text = "Playback data rejected: " + exception.Message; await ShowCachedAsync(); }
  }
  private async Task ShowCachedAsync()
  {
    var manifest = await SignageConfigurationStore.ReadVerifiedManifestAsync();
    if (manifest is not null)
    {
      await Player.ExecuteScriptAsync($"window.upmRenderManifest({manifest});document.getElementById('offline').classList.add('show')");
    }
  }

  private async void PlayerProcessFailed(WebView2 sender, CoreWebView2ProcessFailedEventArgs args)
  {
    if (DateTimeOffset.UtcNow - recoveryWindow > TimeSpan.FromMinutes(5)) { rendererRecoveries = 0; recoveryWindow = DateTimeOffset.UtcNow; }
    if (++rendererRecoveries > 3) { Diagnostics.Text = "Renderer failed repeatedly. Automatic recovery stopped after 3 attempts; operator action is required."; return; }
    Diagnostics.Text = $"Renderer failed ({args.ProcessFailedKind}); recovery {rendererRecoveries}/3.";
    await Task.Delay(TimeSpan.FromSeconds(rendererRecoveries * 2)); Player.Reload();
  }

  private async void ApplyPlayerClick(object sender, RoutedEventArgs e)
  {
    configuration = configuration with { Monitor = Monitor.SelectedIndex, Fullscreen = Fullscreen.IsOn, Width = (int)PlayerWidth.Value, Height = (int)PlayerHeight.Value, StartWithWindows = StartWithWindows.IsOn };
    await store.SaveAsync(configuration); WindowsStartupService.SetEnabled(configuration.StartWithWindows);
    var windowId = Win32Interop.GetWindowIdFromWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)); var appWindow = AppWindow.GetFromWindowId(windowId);
    appWindow.SetPresenter(configuration.Fullscreen ? AppWindowPresenterKind.FullScreen : AppWindowPresenterKind.Overlapped);
    var target = monitors[Math.Clamp(configuration.Monitor, 0, monitors.Count - 1)].Bounds;
    if (configuration.Fullscreen) appWindow.MoveAndResize(target);
    else appWindow.MoveAndResize(new RectInt32(target.X, target.Y, configuration.Width, configuration.Height));
    Diagnostics.Text = "Player settings applied.";
  }
}
