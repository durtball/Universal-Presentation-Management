using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.UI.Xaml;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;

namespace UPM.SiteManager;

public partial class App : Application
{
  private readonly IHost host;

  public App()
  {
    InitializeComponent();
    host = Host.CreateDefaultBuilder()
        .ConfigureServices(services =>
        {
          var root = Path.Combine(
                  Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                  "UPM",
                  "SiteManager");
          services.AddSingleton<ICredentialVault, CredentialVault>();
          services.AddSingleton(new LocalStateStore(Path.Combine(root, "state.db")));
          services.AddHttpClient<SiteApiClient>()
                  .ConfigurePrimaryHttpMessageHandler(() => new HttpClientHandler
                  {
                    UseCookies = true,
                    CookieContainer = new(),
                  });
          services.AddSingleton<TransferWorker>();
          services.AddHostedService(provider => provider.GetRequiredService<TransferWorker>());
          services.AddSingleton<MainWindow>();
        })
        .Build();
  }

  protected override async void OnLaunched(LaunchActivatedEventArgs args)
  {
    await host.Services.GetRequiredService<LocalStateStore>().InitializeAsync();
    await host.StartAsync();
    host.Services.GetRequiredService<MainWindow>().Activate();
  }
}
