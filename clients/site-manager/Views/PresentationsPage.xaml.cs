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
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<PresentationRow> rows = [];
  private readonly List<PresentationRow> allRows = [];
  private bool subscribed;
  public PresentationsPage() { InitializeComponent(); PresentationList.ItemsSource = rows; }
  private void OnLoaded(object sender,RoutedEventArgs e){if(!subscribed){context.Changed+=OnChanged;subscribed=true;}_=RefreshAsync();}
  private void OnUnloaded(object sender,RoutedEventArgs e){if(subscribed){context.Changed-=OnChanged;subscribed=false;}}
  private void OnChanged(object? sender,OperatorContextChangedEventArgs e)=>DispatcherQueue.TryEnqueue(()=>_=RefreshAsync());
  private void OnRefresh(object sender,RoutedEventArgs e)=>_=RefreshAsync();
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
          Title=item.Text("title","Untitled presentation"), Filename=filename, Version=item.NullableLong("current_version")?.ToString()??"—", Size=JsonProjection.Bytes(item.NullableLong("size_bytes")),
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
  private void Show(string message){StateBar.Title="PRESENTATIONS";StateBar.Message=message;StateBar.Severity=InfoBarSeverity.Informational;StateBar.IsOpen=true;}
}
public sealed class PresentationRow{public string Start{get;set;}="—";public string Room{get;set;}="—";public string Session{get;set;}="—";public string Presenter{get;set;}="—";public string Title{get;set;}="—";public string Filename{get;set;}="—";public string Version{get;set;}="—";public string Size{get;set;}="—";public string Status{get;set;}="UNKNOWN";public Brush StatusBrush{get;set;}=new SolidColorBrush(Colors.Gray);public string FileType{get;set;}="";}
