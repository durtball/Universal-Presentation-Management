using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;
public sealed partial class PresentationsPage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly ObservableCollection<PresentationRow> rows = []; private bool subscribed;
  public PresentationsPage() { InitializeComponent(); PresentationList.ItemsSource = rows; }
  private void OnLoaded(object sender, RoutedEventArgs e) { if (!subscribed) { context.Changed += OnChanged; subscribed=true; } _=RefreshAsync(); }
  private void OnUnloaded(object sender, RoutedEventArgs e) { if(subscribed){context.Changed-=OnChanged;subscribed=false;} }
  private void OnChanged(object? sender, OperatorContextChangedEventArgs e)=>DispatcherQueue.TryEnqueue(()=>_=RefreshAsync());
  private void OnRefresh(object sender,RoutedEventArgs e)=>_=RefreshAsync();
  private void OnSearchChanged(object sender,TextChangedEventArgs e)=>_=RefreshAsync();
  private async Task RefreshAsync()
  {
    rows.Clear(); var api=context.ActiveClient;
    if(api is null){Show("Connect to a Site to view presentations.",InfoBarSeverity.Informational);return;}
    if(context.SelectedEventId is not Guid eventId){Show("Select an event to view presentations.",InfoBarSeverity.Informational);return;}
    try { var payload=await api.GetPresentationOperationsAsync(eventId,SearchBox.Text,CancellationToken.None);
      foreach(var item in payload) { var presenters=item.Child("presenters"); rows.Add(new PresentationRow{Status=$"{item.Text("readiness","unknown").ToUpperInvariant()} / {item.Text("delivery_state","unknown")}",Start=JsonProjection.LocalTime(item.Text("starts_at","")).Split(' ').LastOrDefault()??"—",Room=item.Text("room"),Title=item.Text("title"),Filename=item.Text("filename","No canonical file"),Session=item.Text("session"),Presenter=presenters.ValueKind==System.Text.Json.JsonValueKind.Array?string.Join(", ",presenters.EnumerateArray().Select(x=>x.ToString())):"—",Version=item.Text("current_version","—"),Size=JsonProjection.Bytes(item.Number("size_bytes")),Modified=JsonProjection.LocalTime(item.Text("updated_at",""))}); }
      CountText.Text=$"{rows.Count} presentations"; if(rows.Count==0)Show("This event has no matching presentations.",InfoBarSeverity.Informational);else StateBar.IsOpen=false;
    } catch(Exception ex){Show(ex.Message,InfoBarSeverity.Error);} }
  private void Show(string message,InfoBarSeverity severity){StateBar.Title=severity==InfoBarSeverity.Error?"PRESENTATION API ERROR":"PRESENTATIONS";StateBar.Message=message;StateBar.Severity=severity;StateBar.IsOpen=true;}
}
public sealed class PresentationRow { public string Status{get;set;}="";public string Start{get;set;}="";public string Room{get;set;}="";public string Title{get;set;}="";public string Filename{get;set;}="";public string Session{get;set;}="";public string Presenter{get;set;}="";public string Version{get;set;}="";public string Size{get;set;}="";public string Modified{get;set;}=""; }
