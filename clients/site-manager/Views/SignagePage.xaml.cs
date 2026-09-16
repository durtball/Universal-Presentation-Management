using System.Diagnostics;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class SignagePage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private SignageStatus? status;
  public SignagePage() => InitializeComponent();
  private async void OnLoaded(object sender, RoutedEventArgs e) => await RefreshAsync();
  private async void RetryClick(object sender, RoutedEventArgs e) => await RefreshAsync();
  private async Task RefreshAsync()
  {
    if (context.ActiveClient is not { } api) { Show("Signage service unavailable", "Connect to a Site, then Retry.", InfoBarSeverity.Warning); return; }
    try
    {
      status = await api.GetSignageStatusAsync(CancellationToken.None);
      if (!status.Healthy) { Show("Signage service unavailable", status.Message ?? "Use Diagnostics for connection details.", InfoBarSeverity.Warning); return; }
      Show("Signage service online", status.SourceConnected ? "Site source connected." : "Site source offline; cached playback remains ready.", InfoBarSeverity.Success);
    }
    catch { Show("Signage service unavailable", "Retry the health check or open Diagnostics.", InfoBarSeverity.Error); }
  }
  private void OpenClick(object sender, RoutedEventArgs e)
  {
    if (status?.Healthy == true && Uri.TryCreate(status.Url, UriKind.Absolute, out var uri)) Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
    else Show("Signage service unavailable", "Select Retry before opening Signage Manager.", InfoBarSeverity.Warning);
  }
  private void DiagnosticsClick(object sender, RoutedEventArgs e) => Details.Text = status is null ? "No Signage health response is available." : $"Installed: {status.Installed}\nHealthy: {status.Healthy}\nSite source: {(status.SourceConnected ? "Connected" : "Offline")}";
  private void Show(string title, string message, InfoBarSeverity severity) { State.Title = title; State.Message = message; State.Severity = severity; State.IsOpen = true; }
}
