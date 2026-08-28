using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Microsoft.UI.Xaml;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;

namespace UPM.SiteManager;

public partial class App : Application
{
  private readonly IHost host;
  private readonly StartupDiagnostics diagnostics;
  private string startupStage = "application construction";

  public App()
  {
    var root = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "UPM",
        "SiteManager");
    diagnostics = new StartupDiagnostics(root);
    try
    {
      startupStage = "WinUI resource initialization";
      InitializeComponent();
      UnhandledException += OnUnhandledException;
      startupStage = "dependency injection configuration";
      host = Host.CreateDefaultBuilder()
          .ConfigureLogging(logging => logging.AddProvider(new LocalFileLoggerProvider(root)))
                .ConfigureServices(services =>
          {
            services.AddSingleton(diagnostics);
            services.AddSingleton<ICredentialVault, CredentialVault>();
            services.AddSingleton(new LocalStateStore(Path.Combine(root, "state.db")));
            services.AddSingleton<ISiteClientFactory, SiteClientFactory>();
            services.AddSingleton<ISiteConnectionManager, SiteConnectionManager>();
            services.AddSingleton<ISiteTransferRouter, SiteTransferRouter>();
            services.AddSingleton<TransferWorker>();
            services.AddHostedService(provider => provider.GetRequiredService<TransferWorker>());
            services.AddSingleton<MainWindow>();
          })
          .Build();
    }
    catch (Exception exception)
    {
      diagnostics.WriteFailure(startupStage, exception);
      throw;
    }
  }

  public static IServiceProvider Services => ((App)Current).host.Services;

  protected override async void OnLaunched(LaunchActivatedEventArgs args)
  {
    try
    {
      startupStage = "local state initialization";
      await host.Services.GetRequiredService<LocalStateStore>().InitializeAsync();
      startupStage = "background service startup";
      await host.StartAsync();
      startupStage = "main window creation";
      host.Services.GetRequiredService<MainWindow>().Activate();
      startupStage = "running";
    }
    catch (Exception exception)
    {
      diagnostics.WriteFailure(startupStage, exception);
      throw;
    }
  }

  private void OnUnhandledException(object sender, Microsoft.UI.Xaml.UnhandledExceptionEventArgs args)
  {
    diagnostics.WriteFailure(startupStage, args.Exception);
  }
}
