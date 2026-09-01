using Microsoft.UI.Xaml;
using Microsoft.AspNetCore.Builder;
using UPM.RoomAgent.Service;

namespace UPM.RoomAgent;

public partial class App : Application
{
  private Window? window;
  private WebApplication? agentHost;
  private readonly Mutex singleInstance;
  public static new App Current => (App)Application.Current;
  public bool OwnsMutex { get; }
  public App()
  {
    singleInstance = new(true, "Local\\UPM.RoomAgent", out var ownsMutex);
    OwnsMutex = ownsMutex; InitializeComponent();
  }
  protected override async void OnLaunched(LaunchActivatedEventArgs args)
  {
    if (!OwnsMutex) { Exit(); return; }
    try
    {
      agentHost = await RoomAgentHost.StartAsync();
      window = new MainWindow(new LocalAgentClient()); window.Activate();
    }
    catch (Exception exception)
    {
      var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "UPM", "Agent", "logs");
      Directory.CreateDirectory(path); await File.AppendAllTextAsync(Path.Combine(path, "startup-error.log"), $"{DateTimeOffset.Now:O} {exception}\n");
      throw;
    }
  }
  public async Task ExitAgentAsync()
  {
    if (agentHost is not null) await agentHost.StopAsync();
    singleInstance.ReleaseMutex(); Exit();
  }
}
