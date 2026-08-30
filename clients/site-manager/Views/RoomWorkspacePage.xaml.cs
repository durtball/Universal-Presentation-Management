using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class RoomWorkspacePage : Page
{
  private PresentationOpenService? opener;
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<RoomScheduleRow> rows = [];
  private Guid roomId;
  private Guid? primaryDeviceId;
  private bool subscribed;

  public RoomWorkspacePage()
  {
    InitializeComponent();
    ScheduleList.ItemsSource = rows;
  }

  protected override void OnNavigatedTo(NavigationEventArgs e)
  {
    base.OnNavigatedTo(e);
    roomId = e.Parameter is Guid value ? value : Guid.Empty;
    if (!subscribed)
    {
      context.Changed += OnContextChanged;
      subscribed = true;
    }
    _ = RefreshAsync();
  }

  protected override void OnNavigatedFrom(NavigationEventArgs e)
  {
    if (subscribed)
    {
      context.Changed -= OnContextChanged;
      subscribed = false;
    }
    base.OnNavigatedFrom(e);
  }

  private void OnContextChanged(object? sender, OperatorContextChangedEventArgs e) =>
      DispatcherQueue.TryEnqueue(() => _ = RefreshAsync());
  private void OnBack(object sender, RoutedEventArgs e) => Frame.GoBack();
  private void OnRefresh(object sender, RoutedEventArgs e) => _ = RefreshAsync();
  private void OnView(object sender, RoutedEventArgs e)
  {
    if (sender is Button { CommandParameter: RoomScheduleRow row })
    {
      StateBar.Title = row.Presentation;
      StateBar.Message = $"Presentation {row.PresentationId} • version {row.VersionNumber} • Site copy {row.Readiness}.";
      StateBar.Severity = InfoBarSeverity.Informational;
      StateBar.IsOpen = true;
    }
  }
  private void OnOpen(object sender, RoutedEventArgs e)
  {
    if (sender is Button { CommandParameter: RoomScheduleRow row }) _ = OpenAsync(row);
  }
  private void OnPush(object sender, RoutedEventArgs e)
  {
    if (sender is Button { CommandParameter: RoomScheduleRow row }) _ = SendCommandAsync(row, "preload");
  }
  private void OnPushAndOpen(object sender, RoutedEventArgs e)
  {
    if (sender is Button { CommandParameter: RoomScheduleRow row }) _ = SendCommandAsync(row, "push_and_open");
  }

  private async Task RefreshAsync()
  {
    var api = context.ActiveClient;
    rows.Clear();
    if (api is null || roomId == Guid.Empty)
    {
      Show("Connect to a Site and select a room.", InfoBarSeverity.Informational);
      return;
    }
    try
    {
      var room = await api.GetRoomAsync(roomId, CancellationToken.None);
      RoomTitle.Text = $"ROOM: {room.Text("label", "UNKNOWN").ToUpperInvariant()}";
      var endpoints = room.Child("endpoints");
      var primary = endpoints.Child("primary");
      var backup = endpoints.Child("backup");
      primaryDeviceId = primary.Id("device_id");
      PrimaryAgent.Text = Endpoint("PRIMARY", primary);
      BackupAgent.Text = Endpoint("BACKUP", backup);
      Telemetry.Text = $"Telemetry: {primary.Text("status", "UNKNOWN").ToUpperInvariant()}";
      var sessions = room.Items("sessions")
          .Where(item => context.SelectedEventId is not Guid selected || item.Id("event_id") == selected)
          .OrderBy(item => item.NullableDate("starts_at"))
          .ToArray();
      var now = DateTimeOffset.UtcNow;
      var current = sessions.LastOrDefault(item => item.NullableDate("starts_at") <= now && item.NullableDate("ends_at") >= now);
      var next = sessions.FirstOrDefault(item => item.NullableDate("starts_at") > now);
      ApplySession(current, NowTitle, NowDetail, "No current session");
      ApplySession(next, NextTitle, NextDetail, "No upcoming session");
      foreach (var session in sessions)
      {
        var sessionPresenters = string.Join(", ", session.Items("presenters").Select(item => item.Text("name")).Where(value => value != "—"));
        foreach (var presentation in session.Items("presentations"))
        {
          var latest = presentation.Items("versions").FirstOrDefault();
          var media = latest.Items("media").FirstOrDefault();
          var presentationPresenters = presentation.Items("presenters").Select(item => item.ValueKind == JsonValueKind.String ? item.GetString() : item.Text("name")).Where(value => !string.IsNullOrWhiteSpace(value));
          rows.Add(new RoomScheduleRow
          {
            Time = session.NullableDate("starts_at")?.ToLocalTime().ToString("h:mm tt") ?? "—",
            Session = session.Text("title"),
            Presenters = presentationPresenters.Any() ? string.Join(", ", presentationPresenters) : string.IsNullOrWhiteSpace(sessionPresenters) ? "—" : sessionPresenters,
            Presentation = presentation.Text("title"),
            PresentationId = presentation.Id("presentation_id") ?? Guid.Empty,
            PresentationVersionId = latest.Id("presentation_version_id"),
            VersionNumber = latest.NullableLong("version_number")?.ToString() ?? "—",
            Filename = media.Text("original_filename", media.Text("filename", $"{presentation.Text("title", "presentation")}.pptx")),
            SizeBytes = media.NullableLong("size_bytes"),
            Sha256 = media.Text("sha256", ""),
            Readiness = presentation.Text("operational_status", "UNKNOWN").ToUpperInvariant(),
          });
        }
      }
      RoomStatus.Text = $"{rows.Count} Presentation Entries • Site media and room cache are distinct states";
      StateBar.IsOpen = false;
    }
    catch (Exception exception)
    {
      Show(exception.Message, InfoBarSeverity.Error);
    }
  }

  private async Task OpenAsync(RoomScheduleRow row)
  {
    if (context.ActiveClient is not { } api || row.PresentationVersionId is not Guid versionId)
    {
      Show("NO COMMITTED VERSION — this Presentation Entry has no canonical Site version to open here.", InfoBarSeverity.Warning);
      return;
    }
    try
    {
      var progress = new Progress<PresentationOpenProgress>(update =>
          Show(update.Percent.HasValue ? $"{update.State} {update.Percent:0}%" : update.State, InfoBarSeverity.Informational));
      var result = await (opener ??= new PresentationOpenService()).OpenAsync(api, new(versionId, row.Filename, row.SizeBytes, string.IsNullOrWhiteSpace(row.Sha256) ? null : row.Sha256), progress, CancellationToken.None);
      Show(result.Message, result.Launched ? InfoBarSeverity.Success : InfoBarSeverity.Warning);
    }
    catch (SiteEndpointException exception) { Show($"MEDIA NOT AVAILABLE LOCALLY — {exception.Message}", InfoBarSeverity.Error); }
    catch (InvalidDataException exception) { Show($"DOWNLOAD VERIFICATION FAILED — {exception.Message}", InfoBarSeverity.Error); }
    catch (Exception exception) { Show(exception.Message, InfoBarSeverity.Error); }
  }
  private async Task SendCommandAsync(RoomScheduleRow row, string commandType)
  {
    if (context.ActiveClient is not { } api)
    {
      Show("Site connection lost.", InfoBarSeverity.Error);
      return;
    }
    if (primaryDeviceId is not Guid deviceId)
    {
      Show("No primary Agent is assigned to this room.", InfoBarSeverity.Warning);
      return;
    }
    if (row.PresentationVersionId is not Guid versionId)
    {
      Show("The Presentation Entry has no canonical version to push.", InfoBarSeverity.Warning);
      return;
    }
    try
    {
      await api.CreateCommandAsync(
          new DeviceCommandRequest(
              deviceId,
              roomId,
              commandType,
              new Dictionary<string, object>
              {
                ["presentation_id"] = row.PresentationId,
                ["presentation_version_id"] = versionId,
              },
              $"site-manager:{commandType}:{roomId}:{versionId}"),
          CancellationToken.None);
      Show("Durable Site command accepted. Agent acknowledgement will update device activity.", InfoBarSeverity.Success);
    }
    catch (Exception exception)
    {
      Show(exception.Message, InfoBarSeverity.Error);
    }
  }

  private static string Endpoint(string role, JsonElement endpoint) =>
      endpoint.ValueKind == JsonValueKind.Object
          ? $"{role}: {endpoint.Text("name", endpoint.Text("device_id"))} • {endpoint.Text("status", "UNKNOWN").ToUpperInvariant()}"
          : $"{role}: UNASSIGNED";

  private static void ApplySession(JsonElement value, TextBlock title, TextBlock detail, string empty)
  {
    if (value.ValueKind != JsonValueKind.Object)
    {
      title.Text = "—";
      detail.Text = empty;
      return;
    }
    title.Text = value.Text("title");
    detail.Text = value.NullableDate("starts_at")?.ToLocalTime().ToString("h:mm tt") ?? "Time unknown";
  }

  private void Show(string message, InfoBarSeverity severity)
  {
    StateBar.Title = severity == InfoBarSeverity.Error ? "ROOM OPERATION FAILED" : "ROOM OPERATION";
    StateBar.Message = message;
    StateBar.Severity = severity;
    StateBar.IsOpen = true;
  }
}

public sealed class RoomScheduleRow
{
  public string Time { get; set; } = "—";
  public string Session { get; set; } = "—";
  public string Presenters { get; set; } = "—";
  public string Presentation { get; set; } = "—";
  public Guid PresentationId { get; set; }
  public Guid? PresentationVersionId { get; set; }
  public string VersionNumber { get; set; } = "—";
  public string Filename { get; set; } = "presentation.pptx";
  public long? SizeBytes { get; set; }
  public string Sha256 { get; set; } = "";
  public string Readiness { get; set; } = "UNKNOWN";
}
