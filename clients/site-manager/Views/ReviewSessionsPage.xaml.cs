using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.SiteApi;
namespace UPM.SiteManager.Views;
public sealed partial class ReviewSessionsPage:Page
{
 private readonly IOperatorContext context=App.Services.GetRequiredService<IOperatorContext>();private readonly ObservableCollection<ReviewRow> rows=[];private bool subscribed;
 public ReviewSessionsPage(){InitializeComponent();ReviewList.ItemsSource=rows;}
 private void OnLoaded(object sender,RoutedEventArgs e){if(!subscribed){context.Changed+=OnChanged;subscribed=true;}_=RefreshAsync();}
 private void OnUnloaded(object sender,RoutedEventArgs e){if(subscribed){context.Changed-=OnChanged;subscribed=false;}}
 private void OnChanged(object? sender,OperatorContextChangedEventArgs e)=>DispatcherQueue.TryEnqueue(()=>_=RefreshAsync());private void OnRefresh(object sender,RoutedEventArgs e)=>_=RefreshAsync();
 private async Task RefreshAsync(){rows.Clear();var api=context.ActiveClient;if(api is null){Show("Connect to a Site to view review sessions.",InfoBarSeverity.Informational);return;}try{var payload=await api.GetReviewSessionsAsync(CancellationToken.None);foreach(var x in payload.Items())rows.Add(new ReviewRow{Presentation=x.Text("presentation_title",x.Text("presentation_id")),BaseVersion="Base "+x.Text("base_presentation_version_id"),Room=x.Text("room_label",x.Text("room_id")),Device=x.Text("device_name",x.Text("device_hostname",x.Text("device_id"))),State=x.Text("state","UNKNOWN").ToUpperInvariant(),Opened=JsonProjection.LocalTime(x.Text("opened_at","")),Modified=JsonProjection.LocalTime(x.Text("working_modified_at",x.Text("local_changes_at",""))),Size=JsonProjection.Bytes(x.NullableLong("working_size_bytes")),Hash=x.Text("working_sha256"),Filename=x.Text("working_filename"),Conflict=x.Text("conflict_version_id","").Length>0});CountText.Text=$"{rows.Count} ACTIVE AND RECENT REVIEWS";if(rows.Count==0)Show("No active review sessions.",InfoBarSeverity.Informational);else{StateBar.IsOpen=false;ReviewList.SelectedIndex=0;}}catch(SiteEndpointException ex)when(ex.Message.Contains("newer UPM Site",StringComparison.Ordinal)){Show("Review listing requires a newer UPM Site build.",InfoBarSeverity.Warning);}catch(Exception ex){Show(ex.Message,InfoBarSeverity.Error);}}
 private void OnSelectionChanged(object sender,SelectionChangedEventArgs e){if(ReviewList.SelectedItem is not ReviewRow row){ChangeState.Text="SELECT A REVIEW";return;}ChangeState.Text=row.Conflict?"REVISION CONFLICT":row.Hash=="—"?"WAITING FOR CHANGES":"LOCAL CHANGES DETECTED";ModifiedText.Text=$"Last Modified  {row.Modified}";SizeText.Text=$"File Size  {row.Size}";HashText.Text=$"Hash  {row.Hash}";TimelineText.Text=$"{row.Opened}  Review {row.State}\n{row.Filename}";}
 private void Show(string message,InfoBarSeverity severity){StateBar.Title=severity==InfoBarSeverity.Error?"REVIEW API ERROR":"REVIEWS";StateBar.Message=message;StateBar.Severity=severity;StateBar.IsOpen=true;}
}
public sealed class ReviewRow{public string Presentation{get;set;}="—";public string BaseVersion{get;set;}="—";public string Room{get;set;}="—";public string Device{get;set;}="—";public string State{get;set;}="UNKNOWN";public string Opened{get;set;}="—";public string Modified{get;set;}="—";public string Size{get;set;}="—";public string Hash{get;set;}="—";public string Filename{get;set;}="—";public bool Conflict{get;set;}}
