using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using global::Windows.ApplicationModel.DataTransfer;
using Microsoft.Extensions.DependencyInjection;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;
using Microsoft.UI;
using Microsoft.UI.Xaml.Media;

namespace UPM.SiteManager.Views;

public sealed partial class IntakePage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly LocalStateStore store = App.Services.GetRequiredService<LocalStateStore>();
  private readonly List<SiteIntakeRow> siteIntake = [];
  private readonly DispatcherTimer refreshTimer = new() { Interval = TimeSpan.FromSeconds(12) };
  private bool reloading;
  private bool assignmentEditing;

  public IntakePage()
  {
    InitializeComponent();
    Loaded += OnLoaded;
    Unloaded += OnUnloaded;
    refreshTimer.Tick += (_, _) => _ = ReloadAsync();
  }

  private void OnDragOver(object sender, DragEventArgs args)
  {
    args.AcceptedOperation = DataPackageOperation.Copy;
    args.DragUIOverride.Caption = "Queue in UPM Site Manager";
    args.DragUIOverride.IsCaptionVisible = true;
    DropSurface.BorderBrush = new SolidColorBrush(Colors.Cyan);
    DropSurface.BorderThickness = new Thickness(2);
  }

  private async void OnDrop(object sender, DragEventArgs args)
  {
    DropSurface.BorderBrush = (Brush)Application.Current.Resources["VioletBrush"];
    DropSurface.BorderThickness = new Thickness(1);
    if (!args.DataView.Contains(StandardDataFormats.StorageItems))
    {
      return;
    }

    var paths = (await args.DataView.GetStorageItemsAsync()).Select(item => item.Path).ToArray();
    await QueuePathsAsync(paths);
  }

  private async void OnSelectFiles(object sender, RoutedEventArgs args)
  {
    var picker = new global::Windows.Storage.Pickers.FileOpenPicker();
    picker.FileTypeFilter.Add("*");
    WinRT.Interop.InitializeWithWindow.Initialize(
        picker,
        WinRT.Interop.WindowNative.GetWindowHandle(App.Services.GetRequiredService<MainWindow>()));
    var files = await picker.PickMultipleFilesAsync();
    await QueuePathsAsync(files.Select(file => file.Path));
  }

  private async void OnSelectFolder(object sender, RoutedEventArgs args)
  {
    var picker = new global::Windows.Storage.Pickers.FolderPicker();
    picker.FileTypeFilter.Add("*");
    WinRT.Interop.InitializeWithWindow.Initialize(
        picker,
        WinRT.Interop.WindowNative.GetWindowHandle(App.Services.GetRequiredService<MainWindow>()));
    var folder = await picker.PickSingleFolderAsync();
    if (folder is not null)
    {
      await QueuePathsAsync([folder.Path]);
    }
  }

  private async Task QueuePathsAsync(IEnumerable<string> paths)
  {
    if (context.Connection?.State != SiteConnectionState.Connected || context.Profile is null)
    {
      QueueStatus.Text = "Connect to a Site before accepting intake.";
      return;
    }

    await foreach (var item in IntakeEnumerator.EnumerateAsync(
                       paths,
                       context.Profile.ProfileId,
                       context.SelectedEventId))
    {
      await store.EnqueueAsync(item);
    }

    await ReloadAsync();
  }

  private void OnLoaded(object sender, RoutedEventArgs args)
  {
    context.Changed += OnContextChanged;
    refreshTimer.Start();
    _ = ReloadAsync();
  }

  private void OnUnloaded(object sender, RoutedEventArgs args)
  {
    context.Changed -= OnContextChanged;
    refreshTimer.Stop();
  }

  private void OnContextChanged(object? sender, OperatorContextChangedEventArgs args) =>
      DispatcherQueue.TryEnqueue(() => _ = ReloadAsync());

  private async Task ReloadAsync()
  {
    if (reloading) return;
    reloading = true;
    try
    {
    var transfers = await store.ListTransfersAsync();
    Queue.Items.Clear();
    foreach (var item in transfers.Where(item => item.SiteProfileId == context.Profile?.ProfileId))
    {
      var brush = item.State switch
      {
        TransferState.Hashing => new SolidColorBrush(Colors.Violet),
        TransferState.Complete => new SolidColorBrush(Colors.LightGreen),
        TransferState.RetryWaiting => new SolidColorBrush(Colors.Goldenrod),
        TransferState.Failed => new SolidColorBrush(Colors.OrangeRed),
        _ => new SolidColorBrush(Colors.Cyan),
      };
      Queue.Items.Add(new IntakeRow
      {
        Filename = item.RelativePath,
        Size = JsonProjection.Bytes(item.Length),
        Status = item.State.ToString().ToUpperInvariant(),
        Progress = item.Length > 0 ? 100d * item.BytesTransferred / item.Length : 0,
        StatusBrush = brush,
      });
    }

    EmptyState.Visibility = Queue.Items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    QueueStatus.Text = $"{transfers.Count(item => item.State == TransferState.Queued)} queued  •  {transfers.Count(item => item.State is TransferState.Hashing or TransferState.Uploading or TransferState.Verifying)} active  •  {transfers.Count(item => item.State == TransferState.Failed)} failed";
    try { await ReloadSiteIntakeAsync(); }
    catch (Exception exception) { QueueStatus.Text = $"Site intake refresh failed: {exception.Message}"; }
    }
    finally { reloading = false; }
  }

  private async Task ReloadSiteIntakeAsync()
  {
    if (assignmentEditing) return;
    var selectedMediaId = (SiteIntakeList.SelectedItem as SiteIntakeRow)?.MediaId;
    if (context.ActiveClient is not { } api || context.SelectedEventId is not Guid eventId)
    {
      return;
    }
    var payload = await api.GetMediaIntakeAsync(eventId, CancellationToken.None);
    foreach (var item in payload.Items())
    {
      var suggestion = item.Child("suggestion");
      var assigned = item.Child("assigned_presentation");
      var row = new SiteIntakeRow
      {
        MediaId = item.Id("media_object_id"),
        PresentationId = suggestion.Id("presentation_id"),
        AssignedPresentationId = item.Id("assigned_presentation_id"),
        CanRetry = item.Text("commit_state", "").Equals("failed", StringComparison.OrdinalIgnoreCase) || item.Text("commit_state", "").Equals("exhausted", StringComparison.OrdinalIgnoreCase),
        Filename = item.Text("filename"),
        RelativePath = item.Text("source_relative_path"),
        MatchState = item.Id("assigned_presentation_id").HasValue
            ? $"OPERATOR ASSIGNED • {item.Text("commit_state", "PENDING").ToUpperInvariant()}"
            : $"{item.Text("match_state", "NEEDS ASSIGNMENT").ToUpperInvariant()} • {item.Text("commit_state", "NEEDS ASSIGNMENT").ToUpperInvariant()}",
        Evidence = item.Text("match_reason", "No confident match evidence"),
        Suggested = suggestion.ValueKind == System.Text.Json.JsonValueKind.Object
            ? $"{string.Join(", ", suggestion.Items("presenters").Select(value => value.ToString()))}\n{suggestion.Text("title")}\n{suggestion.Text("session_title")}  •  {suggestion.Text("room")}  •  {JsonProjection.LocalTime(suggestion.Text("starts_at", ""))}\nPresentation: {suggestion.Text("presentation_identifier")}"
            : "No suggested Presentation Entry",
        Assigned = assigned.ValueKind == System.Text.Json.JsonValueKind.Object
            ? $"{string.Join(", ", assigned.Items("presenters").Select(value => value.ToString()))}\n{assigned.Text("title")}\n{assigned.Text("session_title")}  •  {assigned.Text("room")}  •  {JsonProjection.LocalTime(assigned.Text("starts_at", ""))}\nPresentation: {assigned.Text("presentation_identifier")}"
            : "Not explicitly assigned",
      };
      var existing = siteIntake.FirstOrDefault(value => value.MediaId == row.MediaId);
      if (existing is null)
      {
        siteIntake.Add(row);
        SiteIntakeList.Items.Add(row);
      }
      else
      {
        var index = SiteIntakeList.Items.IndexOf(existing);
        siteIntake[siteIntake.IndexOf(existing)] = row;
        SiteIntakeList.Items[index] = row;
      }
    }
    if (selectedMediaId.HasValue)
      SiteIntakeList.SelectedItem = siteIntake.FirstOrDefault(item => item.MediaId == selectedMediaId);
  }

  private void OnSiteIntakeSelection(object sender, SelectionChangedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row)
    {
      ConfirmButton.IsEnabled = false;
      AssignButton.IsEnabled = false;
      CreateEntryButton.IsEnabled = false;
      RejectButton.IsEnabled = false;
      DeleteButton.IsEnabled = false;
      RetryCommitButton.IsEnabled = false;
      return;
    }
    InspectFile.Text = row.Filename;
    InspectPath.Text = row.RelativePath;
    InspectEvidence.Text = row.Evidence;
    InspectTarget.Text = row.AssignedPresentationId.HasValue
        ? $"OPERATOR ASSIGNMENT\n{row.Assigned}\n\nMATCH SUGGESTION (advisory)\n{row.Suggested}"
        : $"MATCH SUGGESTION (advisory)\n{row.Suggested}";
    ConfirmButton.IsEnabled = row.MediaId.HasValue && row.PresentationId.HasValue;
    AssignButton.IsEnabled = row.MediaId.HasValue;
    CreateEntryButton.IsEnabled = row.MediaId.HasValue;
    RejectButton.IsEnabled = row.MediaId.HasValue;
    DeleteButton.IsEnabled = row.MediaId.HasValue;
    RetryCommitButton.IsEnabled = row.MediaId.HasValue && row.CanRetry;
  }

  private async void OnConfirmSuggestion(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row ||
        row.MediaId is not Guid mediaId || row.PresentationId is not Guid presentationId ||
        context.ActiveClient is not { } api)
    {
      return;
    }
    await api.ConfirmMediaAssignmentAsync(mediaId, presentationId, CancellationToken.None);
    await ReloadSiteIntakeAsync();
  }

  private async void OnAssign(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row || row.MediaId is not Guid mediaId ||
        context.ActiveClient is not { } api || context.SelectedEventId is not Guid eventId) return;
    var search = new TextBox { PlaceholderText = "Presenter, Presentation Entry, Session, room, time, or identifier" };
    var results = new ListView { Height = 280, SelectionMode = ListViewSelectionMode.Single };
    var status = new TextBlock { Text = "Enter at least one search character.", TextWrapping = TextWrapping.Wrap };
    var reassignmentKeys = new Dictionary<Guid, string>();
    search.TextChanged += async (_, _) =>
    {
      results.Items.Clear();
      if (string.IsNullOrWhiteSpace(search.Text)) return;
      try
      {
        var payload = await api.FindPresentationEntriesAsync(eventId, search.Text, CancellationToken.None);
        foreach (var item in payload.Items())
        {
          var presenters = string.Join(", ", item.Items("presenters").Select(value => value.ValueKind == System.Text.Json.JsonValueKind.String ? value.GetString() : value.Text("display_name")));
          results.Items.Add(new AssignmentCandidate(
              item.Id("presentation_id") ?? Guid.Empty,
              $"{presenters} • {item.Text("title")} • {item.Text("session_title")} • {item.Text("room")} • {JsonProjection.LocalTime(item.Text("starts_at", ""))} • {item.Text("presentation_identifier")}"));
        }
        status.Text = $"{results.Items.Count} Presentation Entries";
      }
      catch (Exception exception) { status.Text = exception.Message; }
    };
    var panel = new StackPanel { Spacing = 6 };
    panel.Children.Add(search); panel.Children.Add(status); panel.Children.Add(results);
    var dialog = new ContentDialog { XamlRoot = XamlRoot, Title = "ASSIGN PRESENTATION ENTRY", Content = panel, PrimaryButtonText = "ASSIGN", CloseButtonText = "CANCEL", DefaultButton = ContentDialogButton.Primary, IsPrimaryButtonEnabled = false };
    results.SelectionChanged += (_, _) => dialog.IsPrimaryButtonEnabled = results.SelectedItem is AssignmentCandidate;
    var accepted = false;
    dialog.PrimaryButtonClick += async (_, clickArgs) =>
    {
      clickArgs.Cancel = true;
      if (results.SelectedItem is not AssignmentCandidate target || target.PresentationId == Guid.Empty) return;
      var deferral = clickArgs.GetDeferral();
      dialog.IsPrimaryButtonEnabled = false;
      status.Text = "Saving authoritative operator assignment…";
      try
      {
        System.Text.Json.JsonElement authoritative;
        if (row.AssignedPresentationId.HasValue)
          authoritative = await api.ChangeMediaAssignmentAsync(
              mediaId,
              target.PresentationId,
              reassignmentKeys.TryGetValue(target.PresentationId, out var existingKey)
                  ? existingKey
                  : reassignmentKeys[target.PresentationId] = $"site-manager:{mediaId}:{target.PresentationId}:{Guid.NewGuid()}",
              CancellationToken.None);
        else
          authoritative = await api.ConfirmMediaAssignmentAsync(mediaId, target.PresentationId, CancellationToken.None);
        if (authoritative.Id("presentation_id") != target.PresentationId)
          throw new InvalidDataException("Site returned a different authoritative Presentation Entry.");
        row.AssignedPresentationId = target.PresentationId;
        row.MatchState = "OPERATOR ASSIGNED • COMMIT PENDING";
        accepted = true;
        dialog.Hide();
      }
      catch (Exception exception)
      {
        status.Text = $"Assignment failed: {exception.Message}";
        dialog.IsPrimaryButtonEnabled = true;
      }
      finally { deferral.Complete(); }
    };
    assignmentEditing = true;
    try { await dialog.ShowAsync(); }
    finally { assignmentEditing = false; }
    if (accepted) await ReloadSiteIntakeAsync();
  }

  private async void OnCreateEntry(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row || row.MediaId is not Guid mediaId ||
        context.ActiveClient is not { } api || context.SelectedEventId is not Guid eventId) return;
    try
    {
      var program = await api.GetEventProgramAsync(eventId, CancellationToken.None);
      var title = new TextBox { Header = "Presentation title", Text = Path.GetFileNameWithoutExtension(row.Filename) };
      var sessions = new ComboBox { Header = "Session", PlaceholderText = "Select Session" };
      foreach (var item in program.Items("sessions")) sessions.Items.Add(new ProgramChoice(item.Id("session_id") ?? Guid.Empty, $"{JsonProjection.LocalTime(item.Text("starts_at", ""))} • {item.Text("title")} • {item.Child("assigned_room").Text("label", item.Text("location_name"))}"));
      var presenters = new ComboBox { Header = "Presenter", PlaceholderText = "Optional presenter" };
      foreach (var item in program.Items("participants")) presenters.Items.Add(new ProgramChoice(item.Id("event_participation_id") ?? Guid.Empty, item.Text("display_name", item.Text("person_display_name"))));
      var panel = new StackPanel { Spacing = 7 }; panel.Children.Add(title); panel.Children.Add(sessions); panel.Children.Add(presenters);
      var dialog = new ContentDialog { XamlRoot = XamlRoot, Title = "CREATE PRESENTATION ENTRY", Content = panel, PrimaryButtonText = "CREATE & COMMIT", CloseButtonText = "CANCEL", DefaultButton = ContentDialogButton.Primary };
      if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
      if (string.IsNullOrWhiteSpace(title.Text) || sessions.SelectedItem is not ProgramChoice selectedSession) { await ShowErrorAsync("VALIDATION", "A title and Session are required."); return; }
      var presenterIds = presenters.SelectedItem is ProgramChoice selectedPresenter ? new[] { selectedPresenter.Id } : Array.Empty<Guid>();
      await api.CreatePresentationEntryAsync(eventId, selectedSession.Id, title.Text.Trim(), presenterIds, mediaId, CancellationToken.None);
      await ReloadSiteIntakeAsync();
    }
    catch (Exception exception) { await ShowErrorAsync("CREATE ENTRY FAILED", exception.Message); }
  }

  private async void OnReject(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row || row.MediaId is not Guid mediaId || context.ActiveClient is not { } api) return;
    var reason = new TextBox { Header = "Reason", Text = "Not required for this event" };
    var dialog = new ContentDialog { XamlRoot = XamlRoot, Title = "REJECT INTAKE ITEM", Content = reason, PrimaryButtonText = "REJECT", CloseButtonText = "CANCEL" };
    if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
    try { await api.RejectMediaIntakeAsync(mediaId, reason.Text, CancellationToken.None); await ReloadSiteIntakeAsync(); }
    catch (Exception exception) { await ShowErrorAsync("REJECTION FAILED", exception.Message); }
  }

  private async void OnDelete(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row || row.MediaId is not Guid mediaId || context.ActiveClient is not { } api) return;
    var confirmation = new TextBox { Header = $"Type {row.Filename} to confirm" };
    var dialog = new ContentDialog { XamlRoot = XamlRoot, Title = "DELETE UNCONFIRMED INTAKE", Content = confirmation, PrimaryButtonText = "DELETE", CloseButtonText = "CANCEL" };
    assignmentEditing = true;
    try
    {
      if (await dialog.ShowAsync() != ContentDialogResult.Primary || confirmation.Text != row.Filename) return;
      await api.DeleteMediaIntakeAsync(mediaId, CancellationToken.None);
    }
    catch (Exception exception) { await ShowErrorAsync("DELETION FAILED", exception.Message); }
    finally { assignmentEditing = false; }
    await ReloadSiteIntakeAsync();
  }

  private async void OnRetryCommit(object sender, RoutedEventArgs args)
  {
    if (SiteIntakeList.SelectedItem is not SiteIntakeRow row || row.MediaId is not Guid mediaId || context.ActiveClient is not { } api) return;
    try { await api.RetryMediaCommitAsync(mediaId, CancellationToken.None); await ReloadSiteIntakeAsync(); }
    catch (Exception exception) { await ShowErrorAsync("COMMIT RETRY FAILED", exception.Message); }
  }

  private async Task ShowErrorAsync(string title, string message) => await new ContentDialog { XamlRoot = XamlRoot, Title = title, Content = message, CloseButtonText = "CLOSE" }.ShowAsync();
}

public sealed class IntakeRow
{
  public string Filename { get; set; } = "—";
  public string Size { get; set; } = "—";
  public string Status { get; set; } = "UNKNOWN";
  public double Progress { get; set; }
  public Brush StatusBrush { get; set; } = new SolidColorBrush(Colors.Gray);
}

public sealed class SiteIntakeRow
{
  public Guid? MediaId { get; set; }
  public Guid? PresentationId { get; set; }
  public Guid? AssignedPresentationId { get; set; }
  public bool CanRetry { get; set; }
  public string Filename { get; set; } = "—";
  public string RelativePath { get; set; } = "—";
  public string MatchState { get; set; } = "UNASSIGNED";
  public string Evidence { get; set; } = "—";
  public string Suggested { get; set; } = "—";
  public string Assigned { get; set; } = "—";
}

public sealed record AssignmentCandidate(Guid PresentationId, string Label) { public override string ToString() => Label; }
public sealed record ProgramChoice(Guid Id, string Label) { public override string ToString() => Label; }
