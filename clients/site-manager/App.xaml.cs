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

  public App()
  {
    var root = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "UPM",
        "SiteManager");
    diagnostics = new StartupDiagnostics(root);
    AppDomain.CurrentDomain.UnhandledException += (_, eventArgs) =>
    {
      if (eventArgs.ExceptionObject is Exception exception)
        diagnostics.WriteFailure(diagnostics.Phase, exception);
    };
    TaskScheduler.UnobservedTaskException += (_, eventArgs) =>
    {
      diagnostics.WriteFailure(diagnostics.Phase, eventArgs.Exception);
    };
    try
    {
      SetStartupPhase("WinUI resource initialization");
      InitializeComponent();
      UnhandledException += OnUnhandledException;
      SetStartupPhase("dependency injection configuration");
      host = Host.CreateDefaultBuilder()
          .ConfigureLogging(logging => logging.AddProvider(new LocalFileLoggerProvider(root)))
                .ConfigureServices(services =>
          {
            services.AddSingleton(diagnostics);
            services.AddSingleton<ICredentialVault, CredentialVault>();
            services.AddSingleton(new LocalStateStore(Path.Combine(root, "state.db")));
            services.AddSingleton<ISiteClientFactory, SiteClientFactory>();
            services.AddSingleton<ISiteConnectionManager, SiteConnectionManager>();
            services.AddSingleton<IOperatorContext, OperatorContext>();
            services.AddSingleton<ISiteTransferRouter, SiteTransferRouter>();
            services.AddSingleton<TransferWorker>();
            services.AddHostedService(provider => provider.GetRequiredService<TransferWorker>());
            services.AddSingleton<MainWindow>();
          })
          .Build();
    }
    catch (Exception exception)
    {
      diagnostics.WriteFailure(diagnostics.Phase, exception);
      throw;
    }
  }

  public static IServiceProvider Services => ((App)Current).host.Services;

  protected override async void OnLaunched(LaunchActivatedEventArgs args)
  {
    try
    {
      SetStartupPhase("local state initialization");
      await host.Services.GetRequiredService<LocalStateStore>().InitializeAsync();
      SetStartupPhase("background service startup");
      await host.StartAsync();
      SetStartupPhase("main window creation");
      var window = host.Services.GetRequiredService<MainWindow>();
      SetStartupPhase("main window activation");
      window.Activate();
      SetStartupPhase("running");
    }
    catch (Exception exception)
    {
      diagnostics.WriteFailure(diagnostics.Phase, exception);
      throw;
    }
  }

  private void OnUnhandledException(object sender, Microsoft.UI.Xaml.UnhandledExceptionEventArgs args)
  {
    diagnostics.WriteFailure(diagnostics.Phase, args.Exception);
  }

  private void SetStartupPhase(string phase)
  {
    diagnostics.EnterPhase(phase);
  }
}
