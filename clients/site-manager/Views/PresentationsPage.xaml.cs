using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;
public sealed partial class PresentationsPage : Page
{
  private PresentationOpenService? opener;
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<PresentationRow> rows = [];
  private readonly List<PresentationRow> allRows = [];
  private bool subscribed;
  public PresentationsPage() { InitializeComponent(); PresentationList.ItemsSource = rows; }
  private void OnLoaded(object sender,RoutedEventArgs e){if(!subscribed){context.Changed+=OnChanged;subscribed=true;}_=RefreshAsync();}
  private void OnUnloaded(object sender,RoutedEventArgs e){if(subscribed){context.Changed-=OnChanged;subscribed=false;}}
  private void OnChanged(object? sender,OperatorContextChangedEventArgs e)=>DispatcherQueue.TryEnqueue(()=>_=RefreshAsync());
  private void OnRefresh(object sender,RoutedEventArgs e)=>_=RefreshAsync();
  private async void OnImportProgram(object sender,RoutedEventArgs e)
  {
    if(context.ActiveClient is not{} api||context.SelectedEventId is not Guid eventId){Show("Select an event before importing its program.");return;}
    var picker=new global::Windows.Storage.Pickers.FileOpenPicker();picker.FileTypeFilter.Add(".csv");picker.FileTypeFilter.Add(".xlsx");WinRT.Interop.InitializeWithWindow.Initialize(picker,WinRT.Interop.WindowNative.GetWindowHandle(App.Services.GetRequiredService<MainWindow>()));var file=await picker.PickSingleFileAsync();if(file is null)return;
    try
    {
      await using var source=await file.OpenStreamForReadAsync();var staged=await api.UploadProgramImportAsync(eventId,file.Name,source,CancellationToken.None);var batchId=staged.Id("import_batch_id")??Guid.Empty;if(batchId==Guid.Empty)throw new InvalidDataException("Site did not return an import batch identifier.");Frame.Navigate(typeof(ProgramImportReviewPage),batchId);
    }
    catch(Exception ex){StateBar.Title="PROGRAM IMPORT FAILED";StateBar.Message=ex.Message;StateBar.Severity=InfoBarSeverity.Error;StateBar.IsOpen=true;}
  }
  private void OnFilterChanged(object sender,object e)=>ApplyFilter();
  private async Task RefreshAsync()
  {
    allRows.Clear(); rows.Clear(); var api=context.ActiveClient;
    if(api is null){Show("Connect to a Site to view presentations.");return;}
    if(context.SelectedEventId is not Guid eventId){Show("Select an event to view presentations.");return;}
    try
    {
      var payload=await api.GetPresentationOperationsAsync(eventId,null,CancellationToken.None);
      foreach(var item in payload)
      {
        var readiness=item.Text("readiness","UNKNOWN").ToUpperInvariant();
        var presenters=item.Items("presenters").Select(PresenterText).Where(x=>x!="—");
        var filename=item.Text("filename");
        allRows.Add(new PresentationRow
        {
          Start=FormatTime(item.NullableDate("starts_at")), Room=item.Text("room"), Session=item.Text("session"), Presenter=presenters.Any()?string.Join(", ",presenters):"—",
          Title=item.Text("title","Untitled presentation"), Filename=filename, Version=item.NullableLong("current_version")?.ToString()??"—", Size=JsonProjection.Bytes(item.NullableLong("size_bytes")), SizeBytes=item.NullableLong("size_bytes"),
          PresentationId=item.Id("presentation_id")??Guid.Empty, PresentationVersionId=item.Id("current_presentation_version_id"), RoomId=item.Id("room_id"), Revision=(int)(item.NullableLong("revision")??1),
          Identifier=item.Text("presentation_identifier"), SiteSyncState=item.Text("site_sync_state", "UNKNOWN").ToUpperInvariant(), CentralBackupState=item.Text("central_backup_state", "UNKNOWN").ToUpperInvariant(), CentralBackupError=item.Text("central_backup_error", ""), RoomCacheState=item.Text("room_cache_state", "UNKNOWN").ToUpperInvariant(), Hash=item.Text("sha256"), ReceivedAt=JsonProjection.LocalTime(item.Text("received_at", "")), VersionHistory=string.Join("\n",item.Items("version_history").Select(version=>$"v{version.NullableLong("version_number")??0}  {version.Text("filename")}  {JsonProjection.Bytes(version.NullableLong("size_bytes"))}  {version.Text("availability","UNKNOWN").ToUpperInvariant()}")),
          OperationsDetail=$"{filename} • Site {readiness} • Central {item.Text("central_backup_state", "UNKNOWN").ToUpperInvariant()} • Room cache {item.Text("room_cache_state", "UNKNOWN").ToUpperInvariant()}",
          Status=readiness, StatusBrush=readiness switch{"READY"=>new SolidColorBrush(Colors.LightGreen),"MISSING"=>new SolidColorBrush(Colors.Goldenrod),"REVIEW"=>new SolidColorBrush(Colors.Violet),_=>new SolidColorBrush(Colors.Gray)},
          FileType=Path.GetExtension(filename).ToLowerInvariant(),
        });
      }
      PopulateRooms(); ApplyFilter(); StateBar.IsOpen=allRows.Count==0; if(allRows.Count==0)Show("This event has no presentations.");
    }catch(Exception ex){StateBar.Title="PRESENTATION API ERROR";StateBar.Message=ex.Message;StateBar.Severity=InfoBarSeverity.Error;StateBar.IsOpen=true;}
  }
  private void ApplyFilter(){if(PresentationList is null)return;var room=(RoomFilter.SelectedItem as ComboBoxItem)?.Content?.ToString();var status=(StatusFilter.SelectedItem as ComboBoxItem)?.Content?.ToString();var type=(TypeFilter.SelectedItem as ComboBoxItem)?.Content?.ToString();var search=SearchBox?.Text??"";rows.Clear();foreach(var row in allRows.Where(x=>(room=="All Rooms"||x.Room==room)&&(status=="All Status"||x.Status.Equals(status,StringComparison.OrdinalIgnoreCase))&&(type=="All Types"||type=="PowerPoint"&&x.FileType is ".ppt" or ".pptx"||type=="PDF"&&x.FileType==".pdf")&&(search.Length==0||$"{x.Title} {x.Filename} {x.Presenter} {x.Session}".Contains(search,StringComparison.OrdinalIgnoreCase))))rows.Add(row);CountText.Text=$"{rows.Count} / {allRows.Count}";}
  private void PopulateRooms(){var selected=(RoomFilter.SelectedItem as ComboBoxItem)?.Content?.ToString()??"All Rooms";RoomFilter.Items.Clear();RoomFilter.Items.Add(new ComboBoxItem{Content="All Rooms"});foreach(var room in allRows.Select(x=>x.Room).Where(x=>x!="—").Distinct().Order())RoomFilter.Items.Add(new ComboBoxItem{Content=room});RoomFilter.SelectedItem=RoomFilter.Items.Cast<ComboBoxItem>().FirstOrDefault(x=>x.Content?.ToString()==selected)??RoomFilter.Items[0];}
  private static string PresenterText(JsonElement value)=>value.ValueKind==JsonValueKind.String?value.GetString()??"—":value.ValueKind==JsonValueKind.Object?value.Text("display_name",value.Text("name")):"—";
  private static string FormatTime(DateTimeOffset? value)=>value?.ToLocalTime().ToString("h:mm tt")??"—";
  private async void OnView(object sender,RoutedEventArgs e){if(sender is Button{CommandParameter:PresentationRow row}){var detail=new TextBlock{Text=$"IDENTITY / SCHEDULE\n{row.Title}\n{row.Identifier}\n{row.Start}  •  {row.Room}\n{row.Session}\n{row.Presenter}\n\nCURRENT VERSION\nv{row.Version}  •  {row.Filename}  •  {row.Size}\nSHA-256  {row.Hash}\nReceived {row.ReceivedAt}\n\nSITE STORAGE\n{row.Status}\n\nCENTRAL METADATA SYNC\n{row.SiteSyncState}\n\nCENTRAL MEDIA BACKUP\n{row.CentralBackupState}{(string.IsNullOrWhiteSpace(row.CentralBackupError)?"":$"  •  {row.CentralBackupError}")}\n\nSMB / FILE REFERENCE\nManaged by Site reconciliation; inspect Activity for pending/failure state.\n\nROOM DEPLOYMENT / CACHE\n{row.RoomCacheState}\n\nVERSION HISTORY\n{(string.IsNullOrWhiteSpace(row.VersionHistory)?"No committed versions":row.VersionHistory)}",TextWrapping=TextWrapping.Wrap};await new ContentDialog{XamlRoot=XamlRoot,Title="PRESENTATION DETAIL",Content=new ScrollViewer{Content=detail,MaxHeight=620},CloseButtonText="CLOSE"}.ShowAsync();}}
  private void OnOpen(object sender,RoutedEventArgs e){if(sender is Button{CommandParameter:PresentationRow row})_=OpenAsync(row);}
  private void OnPush(object sender,RoutedEventArgs e){if(sender is Button{CommandParameter:PresentationRow row})_=PushAsync(row);}
  private void OnPushAndOpen(object sender,RoutedEventArgs e){if(sender is Button{CommandParameter:PresentationRow row})_=PushAndOpenAsync(row);}
  private void OnMove(object sender,RoutedEventArgs e){if(sender is Button{CommandParameter:PresentationRow row})_=MoveAsync(row);}
  private async Task OpenAsync(PresentationRow row)
  {
    if (context.ActiveClient is not { } api || row.PresentationVersionId is not Guid versionId)
    {
      Show("NO COMMITTED VERSION — this Presentation Entry has no canonical Site version to open.");
      return;
    }
    try
    {
      var progress = new Progress<PresentationOpenProgress>(update =>
      {
        StateBar.Title = update.State;
        StateBar.Message = update.Percent.HasValue ? $"Downloading canonical Site version… {update.Percent:0}%" : "Resolving canonical Site media…";
        StateBar.Severity = InfoBarSeverity.Informational;
        StateBar.IsOpen = true;
      });
      var result = await (opener ??= new PresentationOpenService()).OpenAsync(api, new(versionId, row.Filename == "—" ? $"{row.Title}.pptx" : row.Filename, row.SizeBytes, row.Hash == "—" ? null : row.Hash), progress, CancellationToken.None);
      StateBar.Title = result.Launched ? "OPENED HERE" : "OPEN FAILED";
      StateBar.Message = result.Message;
      StateBar.Severity = result.Launched ? InfoBarSeverity.Success : InfoBarSeverity.Warning;
      StateBar.IsOpen = true;
    }
    catch (SiteEndpointException exception)
    {
      StateBar.Title = "MEDIA NOT AVAILABLE LOCALLY";
      StateBar.Message = exception.Message;
      StateBar.Severity = InfoBarSeverity.Error;
      StateBar.IsOpen = true;
    }
    catch (InvalidDataException exception)
    {
      StateBar.Title = "DOWNLOAD VERIFICATION FAILED";
      StateBar.Message = exception.Message;
      StateBar.Severity = InfoBarSeverity.Error;
      StateBar.IsOpen = true;
    }
    catch (Exception exception)
    {
      StateBar.Title = "OPEN FAILED";
      StateBar.Message = exception is UnauthorizedAccessException ? "Permission denied while writing the local presentation cache." : exception.Message;
      StateBar.Severity = InfoBarSeverity.Error;
      StateBar.IsOpen = true;
    }
  }
  private async Task PushAsync(PresentationRow row){if(context.ActiveClient is not{} api){Show("Site connection lost.");return;}if(row.RoomId is not Guid roomId||row.PresentationVersionId is not Guid versionId){Show("Assign a room and canonical version before pushing.");return;}try{var room=await api.GetRoomAsync(roomId,CancellationToken.None);var deviceId=room.Child("endpoints").Child("primary").Id("device_id");if(deviceId is not Guid target){Show("No primary Agent is assigned to this room.");return;}await api.CreateCommandAsync(new DeviceCommandRequest(target,roomId,"preload",new Dictionary<string,object>{{"presentation_id",row.PresentationId},{"presentation_version_id",versionId}},$"site-manager:preload:{roomId}:{versionId}"),CancellationToken.None);StateBar.Title="PUSH QUEUED";StateBar.Message="Site durably accepted the command; Agent acknowledgement is shown in Devices/Activity.";StateBar.Severity=InfoBarSeverity.Success;StateBar.IsOpen=true;}catch(Exception ex){StateBar.Title="PUSH FAILED";StateBar.Message=ex.Message;StateBar.Severity=InfoBarSeverity.Error;StateBar.IsOpen=true;}}
  private async Task PushAndOpenAsync(PresentationRow row){if(context.ActiveClient is not{} api||row.RoomId is not Guid roomId||row.PresentationVersionId is not Guid versionId){Show("Assign a room and canonical version before Push & Open.");return;}try{var room=await api.GetRoomAsync(roomId,CancellationToken.None);var target=room.Child("endpoints").Child("primary").Id("device_id");if(target is not Guid deviceId){Show("No primary Agent is assigned to this room.");return;}var operation=Guid.NewGuid();var payload=new Dictionary<string,object>{{"presentation_id",row.PresentationId},{"presentation_version_id",versionId}};await api.CreateCommandAsync(new DeviceCommandRequest(deviceId,roomId,"preload",payload,$"site-manager:push-open:{operation}:preload"),CancellationToken.None);await api.CreateCommandAsync(new DeviceCommandRequest(deviceId,roomId,"open",payload,$"site-manager:push-open:{operation}:open"),CancellationToken.None);StateBar.Title="PUSH & OPEN QUEUED";StateBar.Message="Site durably accepted the preload and open command sequence. Agent execution remains asynchronous.";StateBar.Severity=InfoBarSeverity.Success;StateBar.IsOpen=true;}catch(Exception ex){StateBar.Title="PUSH & OPEN FAILED";StateBar.Message=ex.Message;StateBar.Severity=InfoBarSeverity.Error;StateBar.IsOpen=true;}}
  private async Task MoveAsync(PresentationRow row){if(context.ActiveClient is not{} api||context.SelectedEventId is not Guid eventId){Show("Select an event before moving a Presentation Entry.");return;}try{var program=await api.GetEventProgramAsync(eventId,CancellationToken.None);var choices=new ComboBox{Header="Destination Session",MinWidth=420};foreach(var item in program.Items("sessions"))choices.Items.Add(new MoveChoice(item.Id("session_id")??Guid.Empty,$"{JsonProjection.LocalTime(item.Text("starts_at", ""))} • {item.Text("title")} • {item.Child("assigned_room").Text("label", item.Text("location_name"))}"));var dialog=new ContentDialog{XamlRoot=XamlRoot,Title=$"MOVE {row.Title}",Content=choices,PrimaryButtonText="MOVE",CloseButtonText="CANCEL"};if(await dialog.ShowAsync()!=ContentDialogResult.Primary||choices.SelectedItem is not MoveChoice target||target.Id==Guid.Empty)return;await api.UpdatePresentationAssignmentAsync(row.PresentationId,target.Id,row.Revision,CancellationToken.None);await RefreshAsync();}catch(Exception ex){StateBar.Title="MOVE FAILED";StateBar.Message=ex.Message;StateBar.Severity=InfoBarSeverity.Error;StateBar.IsOpen=true;}}
  private void Show(string message){StateBar.Title="PRESENTATIONS";StateBar.Message=message;StateBar.Severity=InfoBarSeverity.Informational;StateBar.IsOpen=true;}
}
public sealed class PresentationRow{public Guid PresentationId{get;set;}public Guid? PresentationVersionId{get;set;}public Guid? RoomId{get;set;}public int Revision{get;set;}public string Start{get;set;}="—";public string Room{get;set;}="—";public string Session{get;set;}="—";public string Presenter{get;set;}="—";public string Title{get;set;}="—";public string Identifier{get;set;}="—";public string Filename{get;set;}="—";public string OperationsDetail{get;set;}="—";public string Version{get;set;}="—";public string Size{get;set;}="—";public long? SizeBytes{get;set;}public string Hash{get;set;}="—";public string ReceivedAt{get;set;}="—";public string SiteSyncState{get;set;}="UNKNOWN";public string CentralBackupState{get;set;}="UNKNOWN";public string CentralBackupError{get;set;}="";public string RoomCacheState{get;set;}="UNKNOWN";public string VersionHistory{get;set;}="";public string Status{get;set;}="UNKNOWN";public Brush StatusBrush{get;set;}=new SolidColorBrush(Colors.Gray);public string FileType{get;set;}="";}
public sealed record MoveChoice(Guid Id,string Label){public override string ToString()=>Label;}
