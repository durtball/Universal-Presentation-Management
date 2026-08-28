using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.Extensions.DependencyInjection;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed class Metric
{
  public Metric()
  {
  }

  public Metric(string label, string value, string detail, Brush detailBrush)
  {
    Label = label;
    Value = value;
    Detail = detail;
    DetailBrush = detailBrush;
  }

  public string Label { get; set; } = string.Empty;
  public string Value { get; set; } = string.Empty;
  public string Detail { get; set; } = string.Empty;
  public Brush DetailBrush { get; set; } = new SolidColorBrush(Colors.Gray);
}

public sealed partial class DashboardPage : Page
{
  private readonly ISiteConnectionManager connections;

  public DashboardPage()
  {
    connections = App.Services.GetRequiredService<ISiteConnectionManager>();
    InitializeComponent();
    SetDisconnectedMetrics();
    connections.ConnectionChanged += OnConnectionChanged;
    Unloaded += OnUnloaded;
    if (connections.Current is not null)
    {
      Apply(connections.Current);
    }
  }

  public ObservableCollection<Metric> Metrics { get; } = [];

  private void PageSizeChanged(object sender, SizeChangedEventArgs args)
  {
    if (MetricGrid.ItemsPanelRoot is not ItemsWrapGrid panel)
    {
      return;
    }

    var columns = args.NewSize.Width switch
    {
      >= 2100 => 8,
      >= 1500 => 6,
      >= 1000 => 4,
      _ => 2,
    };
    panel.ItemWidth = Math.Max(190, (args.NewSize.Width - (columns * 12)) / columns);
  }

  private void OnConnectionChanged(object? sender, SiteConnectionChangedEventArgs args)
  {
    if (!DispatcherQueue.HasThreadAccess)
    {
      DispatcherQueue.TryEnqueue(() => Apply(args.Status));
    }
    else
    {
      Apply(args.Status);
    }
  }

  private void Apply(SiteConnectionStatus status)
  {
    RefreshButton.IsEnabled = status.State == SiteConnectionState.Connected;
    if (status.State != SiteConnectionState.Connected || status.Snapshot is null)
    {
      SetDisconnectedMetrics();
      return;
    }

    var snapshot = status.Snapshot;
    var dashboard = snapshot.OperationsDashboard;
    var rooms = ArrayLength(dashboard, "rooms");
    var attention = ArrayLength(dashboard, "attention");
    var failedTransfers = IntValue(dashboard, "failed_transfer_jobs");
    var storageHealth = ReadStorageHealth(snapshot.MediaStorage);
    Metrics.Clear();
    Metrics.Add(new("SITE", snapshot.Registration.DisplayName, snapshot.Registration.SiteId.ToString(), Cyan()));
    Metrics.Add(new("DEPLOYED EVENTS", snapshot.EventDeployments.Count.ToString(), "Canonical Site deployments", Violet()));
    Metrics.Add(new("STORAGE HEALTH", storageHealth, "Reported by Site media storage", StatusBrush(storageHealth)));
    Metrics.Add(new("REGISTERED DEVICES", snapshot.Devices.Count.ToString(), "Online state requires heartbeat detail", Cyan()));
    Metrics.Add(new("ROOMS", rooms.ToString(), "Site operational projection", Cyan()));
    Metrics.Add(new("ATTENTION", attention.ToString(), "Current Site attention items", attention > 0 ? Amber() : Green()));
    Metrics.Add(new("FAILED TRANSFERS", failedTransfers.ToString(), "Durable Site transfer jobs", failedTransfers > 0 ? Red() : Green()));
    Metrics.Add(new("CONNECTION", "LIVE", status.Message, Green()));
    EmptyStateTitle.Text = rooms == 0 ? "No rooms in the selected Site projection" : $"{rooms} room projection(s) loaded";
    EmptyStateDetail.Text = rooms == 0
    ? "Select a deployed event to inspect its room operations. Agent status remains UNKNOWN unless telemetry is available."
        : "Open Rooms for authoritative readiness and device assignment detail.";
  }

  private async void OnRefreshClick(object sender, RoutedEventArgs args)
  {
    if (connections.Current?.Profile is not { } profile)
    {
      return;
    }

    try
    {
      RefreshButton.IsEnabled = false;
      await connections.RefreshAsync(profile.ProfileId, CancellationToken.None);
      DashboardInfo.IsOpen = false;
    }
    catch (Exception exception)
    {
      DashboardInfo.Title = "DASHBOARD REFRESH FAILED";
      DashboardInfo.Message = exception.Message;
      DashboardInfo.Severity = InfoBarSeverity.Error;
      DashboardInfo.IsOpen = true;
    }
    finally
    {
      RefreshButton.IsEnabled = connections.Current?.State == SiteConnectionState.Connected;
    }
  }

  private void SetDisconnectedMetrics()
  {
    Metrics.Clear();
    Metrics.Add(new("SITE", "—", "Select or add a Site", Gray()));
    Metrics.Add(new("DEPLOYED EVENTS", "—", "Authentication required", Gray()));
    Metrics.Add(new("STORAGE HEALTH", "—", "Connect to Site", Gray()));
    Metrics.Add(new("REGISTERED DEVICES", "—", "Connect to Site", Gray()));
    Metrics.Add(new("ROOMS", "—", "Connect to Site", Gray()));
    Metrics.Add(new("ATTENTION", "—", "Connect to Site", Gray()));
    Metrics.Add(new("FAILED TRANSFERS", "—", "Connect to Site", Gray()));
    Metrics.Add(new("CONNECTION", "OFFLINE", "No authenticated Site session", Gray()));
  }

  private static int ArrayLength(JsonElement value, string property) =>
      value.TryGetProperty(property, out var array) && array.ValueKind == JsonValueKind.Array
          ? array.GetArrayLength()
          : 0;

  private static int IntValue(JsonElement value, string property) =>
      value.TryGetProperty(property, out var number) && number.TryGetInt32(out var result) ? result : 0;

  private static string ReadStorageHealth(JsonElement storage)
  {
    if (!storage.TryGetProperty("service_available", out var available) || !available.GetBoolean())
    {
      return "UNAVAILABLE";
    }

    if (!storage.TryGetProperty("roots", out var roots) || roots.ValueKind != JsonValueKind.Array)
    {
      return "UNKNOWN";
    }

    var states = roots.EnumerateArray()
        .Select(root => root.TryGetProperty("health", out var health) ? health.GetString() : null)
        .Where(value => !string.IsNullOrWhiteSpace(value))
        .ToArray();
    return states.Any(value => value is "Critical" or "Unavailable") ? "ATTENTION" : "HEALTHY";
  }

  private static Brush StatusBrush(string status) => status switch
  {
    "HEALTHY" => Green(),
    "UNKNOWN" => Gray(),
    _ => Red(),
  };

  private static SolidColorBrush Cyan() => new(Colors.Cyan);
  private static SolidColorBrush Violet() => new(Colors.Violet);
  private static SolidColorBrush Green() => new(Colors.LightGreen);
  private static SolidColorBrush Amber() => new(Colors.Goldenrod);
  private static SolidColorBrush Red() => new(Colors.OrangeRed);
  private static SolidColorBrush Gray() => new(Colors.Gray);

  private void OnUnloaded(object sender, RoutedEventArgs args) =>
      connections.ConnectionChanged -= OnConnectionChanged;
}
