using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class RoomsPage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<RoomRow> rooms = [];
  private bool subscribed;

  public RoomsPage() { InitializeComponent(); RoomList.ItemsSource = rooms; }
  private void OnLoaded(object sender, RoutedEventArgs e) { if (!subscribed) { context.Changed += OnContextChanged; subscribed = true; } _ = RefreshAsync(); }
  private void OnUnloaded(object sender, RoutedEventArgs e) { if (subscribed) { context.Changed -= OnContextChanged; subscribed = false; } }
  private void OnContextChanged(object? sender, OperatorContextChangedEventArgs e) => DispatcherQueue.TryEnqueue(() => _ = RefreshAsync());
  private void OnRefresh(object sender, RoutedEventArgs e) => _ = RefreshAsync();
  private void OnOpenRoom(object sender, RoutedEventArgs e)
  {
    if (sender is Button { Tag: Guid roomId })
    {
      Frame.Navigate(typeof(RoomWorkspacePage), roomId);
    }
  }

  private async Task RefreshAsync()
  {
    var api = context.ActiveClient;
    if (api is null) { Show("Connect to a Site to load rooms.", InfoBarSeverity.Informational); rooms.Clear(); return; }
    try
    {
      var result = await api.GetRoomsAsync(CancellationToken.None);
      var selectedSessions = new Dictionary<Guid, List<System.Text.Json.JsonElement>>();
      if (context.SelectedEventId is Guid eventId)
      {
        var program = await api.GetEventProgramAsync(eventId, CancellationToken.None);
        foreach (var session in program.Items("sessions"))
        {
          var roomId = session.Child("assigned_room").Id("room_id");
          if (roomId.HasValue)
          {
            if (!selectedSessions.TryGetValue(roomId.Value, out var values))
            {
              values = [];
              selectedSessions[roomId.Value] = values;
            }

            values.Add(session);
          }
        }
      }
      rooms.Clear();
      foreach (var item in result)
      {
        var summary = item.Child("summary"); var endpoints = item.Child("endpoints");
        var next = summary.Child("next_session");
        IReadOnlyList<System.Text.Json.JsonElement> eventSessions = item.Id("room_id") is Guid roomId && selectedSessions.TryGetValue(roomId, out var mapped)
            ? mapped
            : [];
        var rotation = "Effective: —  •  Source: INHERITED/UNKNOWN";
        var nextDate = next.NullableDate("starts_at");
        if (context.SelectedEventId is Guid selectedEventId && item.Id("room_id") is Guid selectedRoomId && nextDate.HasValue)
        {
          try
          {
            var rotationPayload = await api.GetEffectiveRotationAsync(selectedEventId, DateOnly.FromDateTime(nextDate.Value.Date), selectedRoomId, next.Id("session_id"), CancellationToken.None);
            var effective = rotationPayload.Child("effective");
            rotation = effective.ValueKind == System.Text.Json.JsonValueKind.Object
                ? $"Effective version: {effective.Text("presentation_version_id")}  •  Source: {rotationPayload.Text("effective_source", "INHERITED").ToUpperInvariant()}"
                : "Effective: —  •  Source: NONE";
          }
          catch (SiteEndpointException)
          {
            rotation = "Effective: —  •  Source: SITE UPDATE REQUIRED";
          }
        }
        rooms.Add(new RoomRow
        {
          RoomId = item.Id("room_id") ?? Guid.Empty,
          Label = item.Text("label"), Origin = context.SelectedEventId.HasValue
              ? $"{eventSessions.Count} mapped sessions in selected event"
              : "SITE ROOM", Health = summary.Text("health", "unknown").ToUpperInvariant(),
          Primary = Endpoint(endpoints, "primary"), Backup = Endpoint(endpoints, "backup"),
          Telemetry = Telemetry(endpoints),
          Readiness = $"{summary.Number("ready_count")} ready / {summary.Number("presentation_count")} presentations / {summary.Number("missing_count")} missing",
          Errors = $"{summary.Number("error_count")} errors", NextSession = next.Text("title", "No upcoming session"), NextTime = JsonProjection.LocalTime(next.Text("starts_at", "")),
          Rotation = rotation,
        });
      }
      CountText.Text = $"{rooms.Count} Site rooms" + (context.SelectedEventId.HasValue ? " • selected event readiness" : " • Site-level infrastructure");
      if (rooms.Count == 0) Show("No rooms are configured at this Site.", InfoBarSeverity.Informational); else StateBar.IsOpen = false;
    }
    catch (Exception ex) { Show(ex.Message, InfoBarSeverity.Error); }
  }

  private static string Endpoint(System.Text.Json.JsonElement endpoints, string role) { var value = endpoints.Child(role); return value.ValueKind == System.Text.Json.JsonValueKind.Object ? $"{role.ToUpperInvariant()}: {value.Text("name", value.Text("device_id"))}" : $"{role.ToUpperInvariant()}: Unassigned"; }
  private static string Telemetry(System.Text.Json.JsonElement endpoints) { var states = new[] { endpoints.Child("primary"), endpoints.Child("backup") }.Where(x => x.ValueKind == System.Text.Json.JsonValueKind.Object).Select(x => x.Text("status", "UNKNOWN").ToUpperInvariant()); return "Telemetry: " + (states.Any() ? string.Join(" / ", states) : "UNAVAILABLE"); }
  private void Show(string message, InfoBarSeverity severity) { StateBar.Title = severity == InfoBarSeverity.Error ? "ROOM API ERROR" : "ROOMS"; StateBar.Message = message; StateBar.Severity = severity; StateBar.IsOpen = true; }
}

public sealed class RoomRow { public Guid RoomId { get; set; } public string Label { get; set; } = ""; public string Origin { get; set; } = ""; public string Health { get; set; } = ""; public string Primary { get; set; } = ""; public string Backup { get; set; } = ""; public string Telemetry { get; set; } = ""; public string Readiness { get; set; } = ""; public string Errors { get; set; } = ""; public string NextSession { get; set; } = ""; public string NextTime { get; set; } = ""; public string Rotation { get; set; } = "Effective: —"; }
