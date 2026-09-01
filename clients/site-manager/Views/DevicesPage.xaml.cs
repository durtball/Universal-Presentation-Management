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
        devices.Add(new DeviceRow { DeviceId = id ?? Guid.Empty, Name = runtime.Text("display_name", item.Text("name")), Hostname = item.Text("machine_name", runtime.Text("hostname", "Unknown machine")), OnlineState = runtime.Text("online_state", item.Text("enrollment_state", "unknown")).ToUpperInvariant(), Assignment = $"{item.Text("event_name", "No event")} • {item.Text("assigned_room_name", "Unassigned")}", Role = item.Text("agent_role", "No role"), Agent = "Agent " + runtime.Text("agent_version", item.Text("agent_version", "unknown")), PowerPoint = runtime.Text("powerpoint_available", "unknown") == "true" ? "PowerPoint available " + runtime.Text("powerpoint_version", "") : "PowerPoint unknown/unavailable", Disk = $"Disk {JsonProjection.Bytes(runtime.Number("free_disk_bytes"))} • cache {JsonProjection.Bytes(runtime.Number("local_cache_bytes"))}", Heartbeat = "Last seen " + JsonProjection.LocalTime(runtime.Text("last_heartbeat_at", item.Text("last_seen", ""))), Current = $"IP {item.Text("ip_address", "—")} • Sync {item.Text("sync_status", "—")}", Error = runtime.Text("last_error", "") });
      }
      CountText.Text = $"{devices.Count} registered devices";
      if (devices.Count == 0) Show("No Agent devices are registered with this Site.", InfoBarSeverity.Informational); else StateBar.IsOpen = false;
    }
    catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
  }
  private async void OnAssign(object sender, RoutedEventArgs e)
  {
    if (sender is not Button { Tag: Guid deviceId } || deviceId == Guid.Empty || context.ActiveClient is not { } api) return;
    try
    {
      var events = await api.GetEventDeploymentsAsync(CancellationToken.None); var rooms = await api.GetRoomsAsync(CancellationToken.None);
      var eventBox = new ComboBox { Header = "Event", DisplayMemberPath = "Name", MinWidth = 360 };
      foreach (var item in events) eventBox.Items.Add(new AssignmentChoice(item.EventId, item.EventName ?? item.EventId.ToString()));
      var roomBox = new ComboBox { Header = "Room", DisplayMemberPath = "Name", MinWidth = 360 };
      roomBox.Items.Add(new AssignmentChoice(Guid.Empty, "None — dedicated kiosk"));
      foreach (var item in rooms) roomBox.Items.Add(new AssignmentChoice(item.Id("room_id") ?? Guid.Empty, item.Text("label")));
      var roleBox = new ComboBox { Header = "Role", MinWidth = 360, ItemsSource = new[] { "Room Agent", "Upload Kiosk", "Room Agent + Kiosk" }, SelectedIndex = 0 };
      if (eventBox.Items.Count > 0) eventBox.SelectedIndex = 0; roomBox.SelectedIndex = roomBox.Items.Count > 1 ? 1 : 0;
      var panel = new StackPanel { Spacing = 10 }; panel.Children.Add(eventBox); panel.Children.Add(roomBox); panel.Children.Add(roleBox);
      var dialog = new ContentDialog { XamlRoot = XamlRoot, Title = "Assign UPM Room Agent", Content = panel, PrimaryButtonText = "ASSIGN", CloseButtonText = "CANCEL" };
      if (await dialog.ShowAsync() != ContentDialogResult.Primary || eventBox.SelectedItem is not AssignmentChoice selectedEvent || roomBox.SelectedItem is not AssignmentChoice selectedRoom) return;
      var role = roleBox.SelectedIndex switch { 1 => "upload_kiosk", 2 => "room_agent_kiosk", _ => "room_agent" };
      await api.AssignRoomAgentAsync(deviceId, selectedEvent.Id, selectedRoom.Id == Guid.Empty ? null : selectedRoom.Id, role, CancellationToken.None); await RefreshAsync();
    }
    catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
  }
  private void Show(string message, InfoBarSeverity severity) { StateBar.Title = severity == InfoBarSeverity.Error ? "DEVICE API ERROR" : "DEVICES"; StateBar.Message = message; StateBar.Severity = severity; StateBar.IsOpen = true; }
}
public sealed record AssignmentChoice(Guid Id, string Name);
public sealed class DeviceRow { public Guid DeviceId { get; set; } public string Name { get; set; }=""; public string Hostname { get; set; }=""; public string OnlineState { get; set; }=""; public string Assignment { get; set; }=""; public string Role { get; set; }=""; public string Agent { get; set; }=""; public string PowerPoint { get; set; }=""; public string Disk { get; set; }=""; public string Heartbeat { get; set; }=""; public string Current { get; set; }=""; public string Error { get; set; }=""; }
