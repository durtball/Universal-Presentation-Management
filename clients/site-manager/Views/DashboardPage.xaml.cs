using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed class Metric
{
  public string Label { get; set; } = "";
  public string Value { get; set; } = "—";
  public string Detail { get; set; } = "UNKNOWN";
  public Brush DetailBrush { get; set; } = new SolidColorBrush(Colors.Gray);
}
public sealed class DashboardRoom
{
  public Guid RoomId { get; set; }
  public string Label { get; set; } = "—";
  public string Agents { get; set; } = "PRIMARY UNASSIGNED  •  BACKUP UNASSIGNED";
  public string Counts { get; set; } = "— READY  •  — MISSING  •  — REVIEW";
  public string Next { get; set; } = "No upcoming session";
  public string NextTime { get; set; } = "—";
}

public sealed partial class DashboardPage : Page
{
  private readonly ISiteConnectionManager connections = App.Services.GetRequiredService<ISiteConnectionManager>();
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly LocalStateStore store = App.Services.GetRequiredService<LocalStateStore>();
  public ObservableCollection<Metric> Metrics { get; } = [];
  public ObservableCollection<DashboardRoom> Rooms { get; } = [];
  public ObservableCollection<string> Activity { get; } = [];

  public DashboardPage()
  {
    InitializeComponent();
    SetDisconnectedMetrics();
    connections.ConnectionChanged += OnConnectionChanged;
    Unloaded += OnUnloaded;
    if (connections.Current is { } current) Apply(current);
  }

  private void PageSizeChanged(object sender, SizeChangedEventArgs args)
  {
    if (MetricGrid.ItemsPanelRoot is not ItemsWrapGrid panel) return;
    var columns = args.NewSize.Width switch { >= 1800 => 8, >= 1350 => 6, >= 950 => 4, _ => 2 };
    panel.ItemWidth = Math.Max(160, (args.NewSize.Width - columns * 9) / columns);
  }

  private void OnConnectionChanged(object? sender, SiteConnectionChangedEventArgs args) =>
      DispatcherQueue.TryEnqueue(() => Apply(args.Status));

  private void Apply(SiteConnectionStatus status)
  {
    RefreshButton.IsEnabled = status.State == SiteConnectionState.Connected;
    if (status.State != SiteConnectionState.Connected || status.Snapshot is null)
    {
      Rooms.Clear(); Activity.Clear(); SetDisconnectedMetrics(); return;
    }
    _ = LoadLiveAsync(status.Snapshot);
  }

  private async Task LoadLiveAsync(SiteOperationalSnapshot snapshot)
  {
    try
    {
      var transfers = await store.ListTransfersAsync();
      var roomItems = snapshot.OperationsDashboard.Items("rooms");
      var attention = snapshot.OperationsDashboard.Items("attention").Count;
      var failed = transfers.Count(x => x.State == TransferState.Failed);
      var active = transfers.Count(x => x.State is TransferState.Hashing or TransferState.Uploading or TransferState.Verifying);
      var queued = transfers.Count(x => x.State == TransferState.Queued);
      long ready = 0, missing = 0, errors = 0;
      Rooms.Clear();
      foreach (var room in roomItems)
      {
        var summary = room.Child("summary"); var endpoints = room.Child("endpoints"); var next = summary.Child("next_session");
        ready += summary.NumberOrDefault("ready_count"); missing += summary.NumberOrDefault("missing_count"); errors += summary.NumberOrDefault("error_count");
        Rooms.Add(new DashboardRoom
        {
          RoomId = room.Id("room_id") ?? Guid.Empty,
          Label = room.Text("label"), Agents = $"PRIMARY {Agent(endpoints, "primary")}  •  BACKUP {Agent(endpoints, "backup")}",
          Counts = $"{summary.NumberOrDefault("ready_count")} READY  •  {summary.NumberOrDefault("missing_count")} MISSING  •  — REVIEW",
          Next = next.Text("title", "No upcoming session"), NextTime = JsonProjection.LocalTime(next.Text("starts_at", "")),
        });
      }
      var online = snapshot.Devices.Count(device => string.Equals(device.Text("status", "unknown"), "online", StringComparison.OrdinalIgnoreCase));
      Metrics.Clear();
      Add("INTAKE QUEUE", queued.ToString(), "Queued files", Violet()); Add("ACTIVE UPLOADS", active.ToString(), "Local transfer workers", Cyan()); Add("FAILED UPLOADS", failed.ToString(), "Needs attention", failed > 0 ? Red() : Green());
      Add("PRESENTATIONS READY", ready.ToString(), "Site room projection", Green()); Add("PRESENTATIONS MISSING", missing.ToString(), "Canonical media missing", missing > 0 ? Amber() : Green());
      Add("ROOMS READY", Math.Max(0, roomItems.Count - attention).ToString(), $"{roomItems.Count} total", attention > 0 ? Amber() : Green()); Add("DEVICES ONLINE", snapshot.Devices.Count == 0 ? "—" : online.ToString(), snapshot.Devices.Count == 0 ? "UNKNOWN" : $"{snapshot.Devices.Count} registered", Cyan()); Add("REVIEWS IN PROGRESS", "—", "UNKNOWN", Violet());
      var total = transfers.Sum(x => x.Length); var done = transfers.Sum(x => Math.Min(x.BytesTransferred, x.Length));
      TransferSummary.Text = total > 0 ? $"{JsonProjection.Bytes(done)} / {JsonProjection.Bytes(total)}" : "—"; TransferProgress.Value = total > 0 ? 100d * done / total : 0; TransferDetail.Text = $"{active} active  •  {queued} queued  •  {failed} failed";
      Activity.Clear();
      if (context.ActiveClient is { } api)
      {
        var logs = await api.GetOperationalLogsAsync(context.SelectedEventId, CancellationToken.None);
        foreach (var log in logs.Items().Take(5)) Activity.Add($"{JsonProjection.LocalTime(log.Text("occurred_at", ""))}  {log.Text("message")}");
      }
      DashboardInfo.IsOpen = false;
    }
    catch (Exception exception) { DashboardInfo.Title = "DASHBOARD REFRESH FAILED"; DashboardInfo.Message = exception.Message; DashboardInfo.Severity = InfoBarSeverity.Error; DashboardInfo.IsOpen = true; }
  }

  private static string Agent(JsonElement endpoints, string role) { var agent = endpoints.Child(role); return agent.ValueKind == JsonValueKind.Object ? $"{agent.Text("name", "UNASSIGNED")} {agent.Text("status", "UNKNOWN").ToUpperInvariant()}" : "UNASSIGNED UNKNOWN"; }
  private void Add(string label, string value, string detail, Brush brush) => Metrics.Add(new Metric { Label = label, Value = value, Detail = detail, DetailBrush = brush });
  private async void OnRefreshClick(object sender, RoutedEventArgs args) { if (connections.Current?.Profile is not { } profile) return; try { await connections.RefreshAsync(profile.ProfileId, CancellationToken.None); } catch (Exception ex) { DashboardInfo.Message = ex.Message; DashboardInfo.IsOpen = true; } }
  private void OnOpenRoom(object sender, RoutedEventArgs args) { if (sender is Button { Tag: Guid roomId } && roomId != Guid.Empty) Frame.Navigate(typeof(RoomWorkspacePage), roomId); }
  private void SetDisconnectedMetrics() { Metrics.Clear(); foreach (var label in new[] { "INTAKE QUEUE", "ACTIVE UPLOADS", "FAILED UPLOADS", "PRESENTATIONS READY", "PRESENTATIONS MISSING", "ROOMS READY", "DEVICES ONLINE", "REVIEWS IN PROGRESS" }) Add(label, "—", "UNKNOWN", Gray()); }
  private static SolidColorBrush Cyan()=>new(Colors.Cyan); private static SolidColorBrush Violet()=>new(Colors.Violet); private static SolidColorBrush Green()=>new(Colors.LightGreen); private static SolidColorBrush Amber()=>new(Colors.Goldenrod); private static SolidColorBrush Red()=>new(Colors.OrangeRed); private static SolidColorBrush Gray()=>new(Colors.Gray);
  private void OnUnloaded(object sender, RoutedEventArgs args)=>connections.ConnectionChanged-=OnConnectionChanged;
}
