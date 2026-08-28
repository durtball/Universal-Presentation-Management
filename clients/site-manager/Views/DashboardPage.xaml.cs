using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace UPM.SiteManager.Views;

public sealed record Metric(string Label, string Value, string Detail, Brush DetailBrush);

public sealed partial class DashboardPage : Page
{
  public Metric[] Metrics { get; } =
  [
      new("INTAKE QUEUE", "0", "Durable local queue", new SolidColorBrush(Colors.Violet)),
        new("ACTIVE UPLOADS", "0", "HTTP media ingestion", new SolidColorBrush(Colors.Cyan)),
        new("FAILED UPLOADS", "0", "No failures", new SolidColorBrush(Colors.LightGreen)),
        new("PRESENTATIONS READY", "—", "Select an event", new SolidColorBrush(Colors.Gray)),
        new("ROOMS READY", "—", "Awaiting Site projection", new SolidColorBrush(Colors.Gray)),
        new("DEVICES ONLINE", "—", "Awaiting telemetry", new SolidColorBrush(Colors.Gray)),
        new("REVIEW ACTIVITY", "0", "No active sessions", new SolidColorBrush(Colors.Violet)),
        new("STORAGE HEALTH", "—", "Connect to Site", new SolidColorBrush(Colors.Gray)),
    ];

  public DashboardPage() => InitializeComponent();

}
