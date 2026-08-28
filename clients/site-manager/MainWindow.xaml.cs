using System.Collections.ObjectModel;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using UPM.SiteManager.Views;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using Windows.Graphics;

namespace UPM.SiteManager;

public sealed partial class MainWindow : Window
{
  private static readonly IReadOnlyDictionary<string, Type> Pages =
      new Dictionary<string, Type>(StringComparer.Ordinal)
      {
        ["dashboard"] = typeof(DashboardPage),
        ["intake"] = typeof(IntakePage),
        ["presentations"] = typeof(PresentationsPage),
        ["rooms"] = typeof(RoomsPage),
        ["transfers"] = typeof(TransfersPage),
        ["reviews"] = typeof(ReviewSessionsPage),
        ["devices"] = typeof(DevicesPage),
        ["activity"] = typeof(ActivityPage),
        ["settings"] = typeof(SettingsPage),
      };

  private readonly LocalStateStore store;
  private readonly ISiteConnectionManager connections;
  private readonly ObservableCollection<SiteProfile> profiles = [];
  private readonly ObservableCollection<EventDeployment> events = [];
  private bool updatingSelectors;

  public MainWindow(LocalStateStore store, ISiteConnectionManager connections)
  {
    this.store = store;
    this.connections = connections;
    InitializeComponent();
    SystemBackdrop = new Microsoft.UI.Xaml.Media.MicaBackdrop();
    AppWindow.Resize(new SizeInt32(1280, 800));
    SiteSelector.ItemsSource = profiles;
    EventSelector.ItemsSource = events;
    connections.ConnectionChanged += OnConnectionChanged;
    ContentFrame.Navigate(typeof(DashboardPage));
    PrimaryNavigation.SelectedItem = PrimaryNavigation.MenuItems[0];
  }

  private async void OnWindowLoaded(object sender, RoutedEventArgs args)
  {
    await ReloadProfilesAsync();
    var lastId = await store.GetPreferenceAsync("last_site_profile_id");
    if (Guid.TryParse(lastId, out var profileId))
    {
      var selected = profiles.FirstOrDefault(item => item.ProfileId == profileId);
      if (selected is not null)
      {
        updatingSelectors = true;
        SiteSelector.SelectedItem = selected;
        updatingSelectors = false;
        await RestoreOrPromptAsync(selected, prompt: false);
      }
    }
  }

  private void Navigate(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
  {
    if (args.SelectedItem is not NavigationViewItem item ||
        item.Tag is not string tag ||
        !Pages.TryGetValue(tag, out var pageType))
    {
      return;
    }

    if (ContentFrame.CurrentSourcePageType != pageType)
    {
      ContentFrame.Navigate(pageType);
    }
  }

  private async void OnAddSiteClick(object sender, RoutedEventArgs args) =>
      await ShowProfileDialogAsync(null);

  private async void OnConnectClick(object sender, RoutedEventArgs args)
  {
    if (SiteSelector.SelectedItem is not SiteProfile profile)
    {
      await ShowProfileDialogAsync(null);
      return;
    }

    await RestoreOrPromptAsync(profile, prompt: true);
  }

  private async void OnSiteSelectionChanged(object sender, SelectionChangedEventArgs args)
  {
    if (updatingSelectors || SiteSelector.SelectedItem is not SiteProfile profile)
    {
      return;
    }

    events.Clear();
    EventSelector.IsEnabled = false;
    await store.SetPreferenceAsync("last_site_profile_id", profile.ProfileId.ToString());
    await RestoreOrPromptAsync(profile, prompt: true);
  }

  private async void OnEventSelectionChanged(object sender, SelectionChangedEventArgs args)
  {
    if (updatingSelectors || SiteSelector.SelectedItem is not SiteProfile profile)
    {
      return;
    }

    var eventId = (EventSelector.SelectedItem as EventDeployment)?.EventId;
    await connections.SelectEventAsync(profile.ProfileId, eventId, CancellationToken.None);
  }

  private async Task RestoreOrPromptAsync(SiteProfile profile, bool prompt)
  {
    var status = await connections.RestoreAsync(profile, CancellationToken.None);
    if (prompt && status.State is (SiteConnectionState.AuthenticationRequired or SiteConnectionState.SessionExpired))
    {
      await ShowProfileDialogAsync(profile);
    }
  }

  private async Task ShowProfileDialogAsync(SiteProfile? existing)
  {
    var dialog = new AddSiteDialog(connections, existing) { XamlRoot = WindowLayout.XamlRoot };
    if (await dialog.ShowAsync() != ContentDialogResult.Primary || dialog.Draft is null)
    {
      return;
    }

    var profile = AddSiteDialog.CreateProfile(dialog.Draft);
    try
    {
      if (dialog.Draft.ExistingProfile is { } previous && previous.BaseUri != profile.BaseUri)
      {
        await connections.LogoutAsync(previous.ProfileId, CancellationToken.None);
      }

      await store.UpsertSiteProfileAsync(profile);
      await ReloadProfilesAsync(profile.ProfileId);
      SiteConnectionStatus status;
      if (string.IsNullOrEmpty(dialog.Draft.Password))
      {
        status = await connections.RestoreAsync(profile, CancellationToken.None);
      }
      else
      {
        status = await connections.ConnectAsync(
            profile,
            dialog.Draft.Username,
            dialog.Draft.Password,
            CancellationToken.None);
      }

      if (status.State == SiteConnectionState.Connected)
      {
        await ReloadProfilesAsync(profile.ProfileId);
      }
    }
    catch (Exception exception)
    {
      ConnectionInfoBar.Title = "SITE PROFILE ERROR";
      ConnectionInfoBar.Message = exception.Message;
      ConnectionInfoBar.Severity = InfoBarSeverity.Error;
      ConnectionInfoBar.IsOpen = true;
    }
  }

  private async Task ReloadProfilesAsync(Guid? selectedProfileId = null)
  {
    var selected = selectedProfileId ?? (SiteSelector.SelectedItem as SiteProfile)?.ProfileId;
    var rows = await store.ListSiteProfilesAsync();
    updatingSelectors = true;
    profiles.Clear();
    foreach (var profile in rows)
    {
      profiles.Add(profile);
    }

    SiteSelector.SelectedItem = profiles.FirstOrDefault(item => item.ProfileId == selected);
    updatingSelectors = false;
  }

  private void OnConnectionChanged(object? sender, SiteConnectionChangedEventArgs args)
  {
    if (!DispatcherQueue.HasThreadAccess)
    {
      DispatcherQueue.TryEnqueue(() => ApplyConnectionStatus(args.Status));
      return;
    }

    ApplyConnectionStatus(args.Status);
  }

  private void ApplyConnectionStatus(SiteConnectionStatus status)
  {
    if (SiteSelector.SelectedItem is SiteProfile selected &&
        status.ProfileId != selected.ProfileId)
    {
      return;
    }

    var (title, color, severity) = status.State switch
    {
      SiteConnectionState.Connected => ("SITE CONNECTED", Colors.LimeGreen, InfoBarSeverity.Success),
      SiteConnectionState.Connecting or SiteConnectionState.Authenticating =>
          ("CONNECTING", Colors.DeepSkyBlue, InfoBarSeverity.Informational),
      SiteConnectionState.Reachable => ("SITE REACHABLE", Colors.Cyan, InfoBarSeverity.Success),
      SiteConnectionState.AuthenticationRequired or SiteConnectionState.SessionExpired =>
          ("AUTHENTICATION REQUIRED", Colors.Goldenrod, InfoBarSeverity.Warning),
      SiteConnectionState.IdentityMismatch => ("SITE IDENTITY MISMATCH", Colors.OrangeRed, InfoBarSeverity.Error),
      SiteConnectionState.Unreachable => ("SITE UNREACHABLE", Colors.Red, InfoBarSeverity.Error),
      SiteConnectionState.Error => ("SITE ERROR", Colors.Red, InfoBarSeverity.Error),
      _ => ("DISCONNECTED", Colors.Gray, InfoBarSeverity.Informational),
    };
    ConnectionTitle.Text = title;
    ConnectionIndicator.Fill = new SolidColorBrush(color);
    ConnectionDetail.Text = status.State == SiteConnectionState.Connected && status.Snapshot is not null
        ? $"{status.Snapshot.Registration.DisplayName} / {status.Profile?.BaseUri.Authority}"
        : status.Message;
    ToolTipService.SetToolTip(ConnectionStatus, status.TechnicalDetail ?? status.Message);
    ConnectionInfoBar.Severity = severity;
    ConnectionInfoBar.Title = title;
    ConnectionInfoBar.Message = status.Message;
    ConnectionInfoBar.IsOpen = status.State is SiteConnectionState.Unreachable
        or SiteConnectionState.IdentityMismatch
        or SiteConnectionState.Error
        or SiteConnectionState.SessionExpired
        or SiteConnectionState.AuthenticationRequired;

    updatingSelectors = true;
    events.Clear();
    if (status.State == SiteConnectionState.Connected && status.Snapshot is not null)
    {
      foreach (var deployment in status.Snapshot.EventDeployments)
      {
        events.Add(deployment);
      }

      EventSelector.IsEnabled = true;
      EventSelector.PlaceholderText = events.Count == 0 ? "No deployed events" : "Select event";
      EventSelector.SelectedItem = events.FirstOrDefault(
          item => item.EventId == status.Profile?.LastSelectedEventId);
    }
    else
    {
      EventSelector.IsEnabled = false;
      EventSelector.PlaceholderText = "Connect to select event";
    }

    updatingSelectors = false;
  }
}
