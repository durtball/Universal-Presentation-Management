using System.Text.Json;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
using Windows.Graphics;
using Windows.System;

namespace UPM.Signage;

public sealed partial class MainWindow : Window
{
  private readonly SignageApiClient api = new();
  private readonly SignageConfigurationStore store = new();
  private readonly SignageDiscoveryService discovery = new();
  private SignageConfiguration configuration = new();
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
    Fullscreen.IsOn = configuration.Fullscreen; PlayerWidth.Value = configuration.Width; PlayerHeight.Value = configuration.Height; StartWithWindows.IsOn = configuration.StartWithWindows;
    monitors = MonitorService.FindAll();
    foreach (var display in monitors) Monitor.Items.Add(display.Name);
    Monitor.SelectedIndex = Math.Clamp(configuration.Monitor, 0, Monitor.Items.Count - 1);
    try { _ = CoreWebView2Environment.GetAvailableBrowserVersionString(); }
    catch (Exception exception) { Diagnostics.Text = "Renderer unavailable: install the Microsoft Edge WebView2 Runtime. " + exception.Message; return; }
    await Player.EnsureCoreWebView2Async();
    await Designer.EnsureCoreWebView2Async();
    if (configuration.HasConnected)
    {
      try
      {
        var saved = api.ValidateEndpoint(configuration.ServerUrl);
        await api.HealthAsync(configuration.ServerUrl);
        await ConnectDiscoveredAsync(new(null, saved.Host, saved.Host, saved, "saved", "UPM Signage"));
      }
      catch { await DiscoverAsync(); }
    }
    else await DiscoverAsync();
    var assets = Path.Combine(AppContext.BaseDirectory, "Assets", "player");
    Player.CoreWebView2.SetVirtualHostNameToFolderMapping("player.upm.local", assets, CoreWebView2HostResourceAccessKind.DenyCors);
    Player.Source = new Uri("https://player.upm.local/index.html");
    if (Environment.GetCommandLineArgs().Contains("--player", StringComparer.OrdinalIgnoreCase)) PlayerClick(this, new RoutedEventArgs());
  }

  private async Task DiscoverAsync()
  {
    DiscoveryProgress.IsActive = true; DiscoveryTitle.Text = "Finding UPM Signage…"; DiscoveryMessage.Text = "";
    var found = await discovery.DiscoverAsync(TimeSpan.FromSeconds(3), CancellationToken.None);
    DiscoveryProgress.IsActive = false; DiscoveredServers.ItemsSource = found;
    if (found.Count == 0) { DiscoveryTitle.Text = "No UPM Signage server found"; DiscoveryMessage.Text = "Check that Signage is running, then Retry. Manual connection is available under Advanced."; return; }
    if (found.Count == 1) { await ConnectDiscoveredAsync(found[0]); return; }
    DiscoveryTitle.Text = "Choose a UPM Signage system"; DiscoveryMessage.Text = "Multiple systems were found on this network."; DiscoveredServers.Visibility = Visibility.Visible;
  }

  private async Task ConnectDiscoveredAsync(DiscoveredSignage item)
  {
    configuration = configuration with { ServerUrl = item.Endpoint.AbsoluteUri.TrimEnd('/'), HasConnected = true }; await store.SaveAsync(configuration);
    await OperatorSessionStore.RestoreAsync(Designer.CoreWebView2, item.Endpoint);
    Designer.Source = item.Endpoint; Designer.Visibility = Visibility.Visible; DiscoveryPanel.Visibility = Visibility.Collapsed;
    var status = await api.HealthAsync(configuration.ServerUrl); ServiceStatus.Text = status.SourceConnected ? $"{item.SiteName} · Site Source Connected" : $"{item.SiteName} · Site Source Offline · Playback Ready";
  }

  private async void RetryDiscoveryClick(object sender, RoutedEventArgs e) => await DiscoverAsync();
  private async void DiscoveredServerChanged(object sender, SelectionChangedEventArgs e) { if (DiscoveredServers.SelectedItem is DiscoveredSignage item) await ConnectDiscoveredAsync(item); }
  private async void ManualConnectClick(object sender, RoutedEventArgs e)
  {
    try { var endpoint = api.ValidateEndpoint(ManualEndpoint.Text); await ConnectDiscoveredAsync(new(null, endpoint.Host, endpoint.Host, endpoint, "unknown", "UPM Signage")); }
    catch (Exception exception) { DiscoveryMessage.Text = exception.Message; }
  }

  private async void DesignerNavigationCompleted(WebView2 sender, CoreWebView2NavigationCompletedEventArgs args)
  {
    if (!args.IsSuccess) { ServiceStatus.Text = "Signage service unavailable"; return; }
    await OperatorSessionStore.CaptureAsync(Designer.CoreWebView2, configuration.ServerUrl);
  }

  private async void DesignerMessageReceived(WebView2 sender, CoreWebView2WebMessageReceivedEventArgs args)
  {
    using var message = JsonDocument.Parse(args.WebMessageAsJson);
    var type = message.RootElement.GetProperty("type").GetString();
    if (type == "operator-session-changed") { await OperatorSessionStore.CaptureAsync(Designer.CoreWebView2, configuration.ServerUrl); return; }
    if (type != "store-display-credential") return;
    var id = message.RootElement.GetProperty("displayId").GetGuid();
    var credential = message.RootElement.GetProperty("credential").GetString();
    if (credential is null) return;
    DisplayCredentialStore.Save(id, credential);
    configuration = configuration with { DisplayId = id };
    await store.SaveAsync(configuration);
  }

  private void DesignerProcessFailed(WebView2 sender, CoreWebView2ProcessFailedEventArgs args)
  { ServiceStatus.Text = "Signage Manager renderer failed · restart UPM Signage"; }

  private void OperatorClick(object sender, RoutedEventArgs e) { OperatorPanel.Visibility = Visibility.Visible; PlayerPanel.Visibility = Visibility.Collapsed; }
  private async void PlayerClick(object sender, RoutedEventArgs e) { OperatorPanel.Visibility = Visibility.Collapsed; PlayerPanel.Visibility = Visibility.Visible; ApplyPlayerWindow(); await RefreshPlayerAsync(); }
  private void WindowKeyDown(object sender, Microsoft.UI.Xaml.Input.KeyRoutedEventArgs e) { if (e.Key == VirtualKey.Escape && PlayerPanel.Visibility == Visibility.Visible) { OperatorClick(sender, e); e.Handled = true; } }

  private async void UnpairLocalClick(object sender, RoutedEventArgs e)
  {
    if (configuration.DisplayId is Guid id) DisplayCredentialStore.Remove(id);
    configuration = configuration with { DisplayId = null }; await store.SaveAsync(configuration);
    Diagnostics.Text = "Local player unpaired. The server assignment must also be unpaired in Operator mode.";
  }

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
    ApplyPlayerWindow();
    Diagnostics.Text = "Player settings applied.";
  }
  private void ApplyPlayerWindow()
  {
    var windowId = Win32Interop.GetWindowIdFromWindow(WinRT.Interop.WindowNative.GetWindowHandle(this)); var appWindow = AppWindow.GetFromWindowId(windowId);
    appWindow.SetPresenter(configuration.Fullscreen ? AppWindowPresenterKind.FullScreen : AppWindowPresenterKind.Overlapped);
    var target = monitors[Math.Clamp(configuration.Monitor, 0, monitors.Count - 1)].Bounds;
    if (configuration.Fullscreen) appWindow.MoveAndResize(target);
    else appWindow.MoveAndResize(new RectInt32(target.X, target.Y, configuration.Width, configuration.Height));
  }
}
