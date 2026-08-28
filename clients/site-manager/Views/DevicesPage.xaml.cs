using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class DevicesPage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<DeviceRow> devices = [];
  private bool subscribed;
  public DevicesPage() { InitializeComponent(); DeviceList.ItemsSource = devices; }
  private void OnLoaded(object sender, RoutedEventArgs e) { if (!subscribed) { context.Changed += OnChanged; subscribed = true; } _ = RefreshAsync(); }
  private void OnUnloaded(object sender, RoutedEventArgs e) { if (subscribed) { context.Changed -= OnChanged; subscribed = false; } }
  private void OnChanged(object? sender, OperatorContextChangedEventArgs e) => DispatcherQueue.TryEnqueue(() => _ = RefreshAsync());
  private void OnRefresh(object sender, RoutedEventArgs e) => _ = RefreshAsync();
  private async Task RefreshAsync()
  {
    var api = context.ActiveClient;
    if (api is null) { devices.Clear(); Show("Connect to a Site to load registered devices.", InfoBarSeverity.Informational); return; }
    try
    {
      var list = await api.GetDevicesAsync(CancellationToken.None); devices.Clear();
      foreach (var item in list)
      {
        var id = item.Id("device_id"); var runtime = id.HasValue ? await api.GetDeviceRuntimeAsync(id.Value, CancellationToken.None) : default;
        devices.Add(new DeviceRow { Name = runtime.Text("display_name", item.Text("name")), Hostname = runtime.Text("hostname", item.Text("device_id")), OnlineState = runtime.Text("online_state", "unknown").ToUpperInvariant(), Assignment = item.Text("assigned_room_id", "Unassigned"), Role = item.Text("role", "No role"), Agent = "Agent " + runtime.Text("agent_version", "unknown"), PowerPoint = runtime.Text("powerpoint_available", "unknown") == "true" ? "PowerPoint available " + runtime.Text("powerpoint_version", "") : "PowerPoint unknown/unavailable", Disk = $"Disk {JsonProjection.Bytes(runtime.Number("free_disk_bytes"))} • cache {JsonProjection.Bytes(runtime.Number("local_cache_bytes"))}", Heartbeat = "Heartbeat " + JsonProjection.LocalTime(runtime.Text("last_heartbeat_at", "")), Current = $"Command {runtime.Text("current_command_id", "—")} • Review {runtime.Text("current_review_session_id", "—")}", Error = runtime.Text("last_error", "") });
      }
      CountText.Text = $"{devices.Count} registered devices";
      if (devices.Count == 0) Show("No Agent devices are registered with this Site.", InfoBarSeverity.Informational); else StateBar.IsOpen = false;
    }
    catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
  }
  private void Show(string message, InfoBarSeverity severity) { StateBar.Title = severity == InfoBarSeverity.Error ? "DEVICE API ERROR" : "DEVICES"; StateBar.Message = message; StateBar.Severity = severity; StateBar.IsOpen = true; }
}
public sealed class DeviceRow { public string Name { get; set; }=""; public string Hostname { get; set; }=""; public string OnlineState { get; set; }=""; public string Assignment { get; set; }=""; public string Role { get; set; }=""; public string Agent { get; set; }=""; public string PowerPoint { get; set; }=""; public string Disk { get; set; }=""; public string Heartbeat { get; set; }=""; public string Current { get; set; }=""; public string Error { get; set; }=""; }
