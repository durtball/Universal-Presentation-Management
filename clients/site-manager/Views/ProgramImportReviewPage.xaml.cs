using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Navigation;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class ProgramImportReviewPage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<ProgramImportRow> visibleRows = [];
  private readonly List<ProgramImportRow> allRows = [];
  private Guid batchId;
  private string batchStatus = "STAGED";
  private long errorCount;

  public ProgramImportReviewPage()
  {
    InitializeComponent();
    RowsList.ItemsSource = visibleRows;
  }

  protected override void OnNavigatedTo(NavigationEventArgs args)
  {
    base.OnNavigatedTo(args);
    if (args.Parameter is Guid id)
    {
      batchId = id;
      _ = ReloadAsync();
    }
  }

  private async Task ReloadAsync()
  {
    if (batchId == Guid.Empty || context.ActiveClient is not { } api) return;
    try
    {
      var batch = await api.GetProgramImportAsync(batchId, CancellationToken.None);
      batchStatus = batch.Text("status", "staged").ToUpperInvariant();
      errorCount = batch.NullableLong("error_count") ?? 0;
      allRows.Clear();
      foreach (var item in batch.Items("rows")) allRows.Add(ProgramImportRow.FromJson(item));
      BatchSummary.Text = $"{batch.Text("filename")}  •  {allRows.Count} rows  •  {batch.NullableLong("valid_count") ?? 0} valid  •  {batch.NullableLong("warning_count") ?? 0} warnings  •  {errorCount} blocking errors  •  {batchStatus}";
      CommitButton.IsEnabled = errorCount == 0 && batchStatus is not "COMMITTED";
      ApplyFilter();
    }
    catch (Exception exception) { ShowFailure("IMPORT REVIEW UNAVAILABLE", Friendly(exception)); }
  }

  private void ApplyFilter()
  {
    if (RowsList is null) return;
    var filter = (StateFilter.SelectedItem as ComboBoxItem)?.Content?.ToString() ?? "All Rows";
    var search = SearchBox?.Text?.Trim() ?? "";
    visibleRows.Clear();
    foreach (var row in allRows.Where(row => row.Matches(filter, search))) visibleRows.Add(row);
  }

  private void OnFilterChanged(object sender, object args) => ApplyFilter();
  private void OnRefresh(object sender, RoutedEventArgs args) => _ = ReloadAsync();
  private void OnBack(object sender, RoutedEventArgs args) { if (Frame.CanGoBack) Frame.GoBack(); else Frame.Navigate(typeof(PresentationsPage)); }
  private async void OnSave(object sender, RoutedEventArgs args)
  {
    if (sender is not Button { CommandParameter: ProgramImportRow row }) return;
    await SaveAsync(row, false);
  }
  private async void OnToggleReject(object sender, RoutedEventArgs args)
  {
    if (sender is not Button { CommandParameter: ProgramImportRow row }) return;
    await SaveAsync(row, !row.IsRejected);
  }

  private async Task SaveAsync(ProgramImportRow row, bool reject)
  {
    if (context.ActiveClient is not { } api) return;
    try
    {
      await api.UpdateProgramImportRowAsync(batchId, row.Id, row.Corrections(), reject, CancellationToken.None);
      await ReloadAsync();
    }
    catch (Exception exception) { ShowFailure(reject ? "ROW REJECTION FAILED" : "ROW CORRECTION FAILED", Friendly(exception)); }
  }

  private async void OnCommit(object sender, RoutedEventArgs args)
  {
    if (context.ActiveClient is not { } api) return;
    try
    {
      await api.CommitProgramImportAsync(batchId, CancellationToken.None);
      await ReloadAsync();
      StateBar.Title = "PROGRAM COMMITTED LOCALLY";
      StateBar.Message = "Rooms, sessions, people, and Presentation Entries are operational at Site. Central recovery synchronization remains independent.";
      StateBar.Severity = InfoBarSeverity.Success;
      StateBar.IsOpen = true;
    }
    catch (Exception exception)
    {
      ShowFailure("COMMIT REQUIRES OPERATOR RESOLUTION", $"The Site could not deterministically reconcile one or more staged rows. Filter Errors, add a source presentation identifier or correct/reject the colliding row, then retry. {Friendly(exception)}");
    }
  }

  private void ShowFailure(string title, string message)
  {
    StateBar.Title = title; StateBar.Message = message; StateBar.Severity = InfoBarSeverity.Error; StateBar.IsOpen = true;
  }
  private static string Friendly(Exception exception) => exception is SiteEndpointException ? exception.Message : "The Site operation did not complete. Check Site connectivity and Activity, then retry.";
}

public sealed class ProgramImportRow
{
  public Guid Id { get; init; }
  public long RowNumber { get; init; }
  public string Date { get; set; } = "";
  public string Time { get; set; } = "";
  public string Room { get; set; } = "";
  public string SessionTitle { get; set; } = "";
  public string PresentationTitle { get; set; } = "";
  public string Presenters { get; set; } = "";
  public string SourceIdentifier { get; set; } = "";
  public string ValidationState { get; init; } = "UNKNOWN";
  public string ReconciliationState { get; init; } = "UNRESOLVED";
  public string Message { get; init; } = "—";
  public bool IsRejected { get; init; }
  public string RejectLabel => IsRejected ? "RE-OPEN" : "REJECT";
  public Brush StateBrush { get; init; } = new SolidColorBrush(Colors.Gray);
  public Brush ReconciliationBrush { get; init; } = new SolidColorBrush(Colors.Gray);

  public static ProgramImportRow FromJson(JsonElement item)
  {
    var normalized = item.Child("normalized_values");
    var corrected = item.Child("corrected_values");
    string Value(string key) => corrected.Text(key, normalized.Text(key, ""));
    var rejected = Value("_import_action").Equals("reject", StringComparison.OrdinalIgnoreCase);
    var validation = item.Text("validation_state", "unknown").ToUpperInvariant();
    var messages = item.Items("validation_messages").Select(value => value.ValueKind == JsonValueKind.String ? value.GetString() : value.ToString()).Where(value => !string.IsNullOrWhiteSpace(value)).ToArray();
    var identifier = Value("external_presentation_id"); if (string.IsNullOrWhiteSpace(identifier)) identifier = Value("presentation_code");
    var reconciliation = rejected ? "REJECTED" : messages.Any(value => value!.Contains("ambiguous", StringComparison.OrdinalIgnoreCase) || value.Contains("collid", StringComparison.OrdinalIgnoreCase)) ? "RESOLUTION" : string.IsNullOrWhiteSpace(identifier) ? "DETERMINISTIC" : "IDENTIFIED";
    return new ProgramImportRow
    {
      Id = item.Id("import_row_id") ?? Guid.Empty, RowNumber = item.NullableLong("source_row_number") ?? 0,
      Date = Value("session_date"), Time = Value("start_time"), Room = Value("location_name"), SessionTitle = Value("session_title"), PresentationTitle = Value("presentation_title"), Presenters = Value("display_name"), SourceIdentifier = identifier,
      ValidationState = rejected ? "REJECTED" : validation, ReconciliationState = reconciliation, Message = messages.Length == 0 ? "—" : string.Join(" • ", messages), IsRejected = rejected,
      StateBrush = new SolidColorBrush(rejected ? Colors.Gray : validation == "VALID" ? Colors.LightGreen : validation == "WARNING" ? Colors.Goldenrod : Colors.OrangeRed),
      ReconciliationBrush = new SolidColorBrush(reconciliation is "IDENTIFIED" or "DETERMINISTIC" ? Colors.LightGreen : reconciliation == "RESOLUTION" ? Colors.Goldenrod : Colors.Gray),
    };
  }

  public IReadOnlyDictionary<string, object?> Corrections() => new Dictionary<string, object?>
  {
    ["session_date"] = Date.Trim(), ["start_time"] = Time.Trim(), ["location_name"] = Room.Trim(), ["session_title"] = SessionTitle.Trim(), ["presentation_title"] = PresentationTitle.Trim(), ["display_name"] = Presenters.Trim(), ["external_presentation_id"] = SourceIdentifier.Trim(),
  };
  public bool Matches(string filter, string search)
  {
    var stateMatch = filter switch { "Errors" => ValidationState == "ERROR", "Warnings" => ValidationState == "WARNING", "Valid" => ValidationState == "VALID", "Rejected" => IsRejected, _ => true };
    return stateMatch && (search.Length == 0 || $"{RowNumber} {Date} {Time} {Room} {SessionTitle} {PresentationTitle} {Presenters} {SourceIdentifier} {Message}".Contains(search, StringComparison.OrdinalIgnoreCase));
  }
}
