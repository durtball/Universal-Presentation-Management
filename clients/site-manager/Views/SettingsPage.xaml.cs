using System.Collections.ObjectModel;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager.Views;

public sealed partial class SettingsPage : Page
{
  private readonly LocalStateStore store;
  private readonly ISiteConnectionManager connections;
  private readonly ObservableCollection<SiteProfile> profiles = [];

  public SettingsPage()
  {
    store = App.Services.GetRequiredService<LocalStateStore>();
    connections = App.Services.GetRequiredService<ISiteConnectionManager>();
    InitializeComponent();
    ProfileList.ItemsSource = profiles;
  }

  private async void OnLoaded(object sender, RoutedEventArgs args) => await ReloadAsync();

  private async void OnAddClick(object sender, RoutedEventArgs args) => await ShowEditorAsync(null);

  private async void OnEditClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is { } profile)
    {
      await ShowEditorAsync(profile);
    }
  }

  private async void OnTestClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is not { } profile)
    {
      return;
    }

    var status = await connections.TestAsync(profile, CancellationToken.None);
    Show(status.Message, status.State == SiteConnectionState.Reachable ? InfoBarSeverity.Success : InfoBarSeverity.Error);
  }

  private async void OnConnectClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is not { } profile)
    {
      return;
    }

    var status = await connections.RestoreAsync(profile, CancellationToken.None);
    if (status.State is SiteConnectionState.AuthenticationRequired or SiteConnectionState.SessionExpired)
    {
      await ShowEditorAsync(profile);
    }
    else
    {
      Show(status.Message, status.State == SiteConnectionState.Connected ? InfoBarSeverity.Success : InfoBarSeverity.Error);
    }
  }

  private async void OnLogoutClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is not { } profile)
    {
      return;
    }

    await connections.LogoutAsync(profile.ProfileId, CancellationToken.None);
    Show("Site session removed from Windows Credential Manager.", InfoBarSeverity.Success);
  }

  private async void OnDisconnectClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is not { } profile)
    {
      return;
    }

    await connections.DisconnectAsync(profile.ProfileId, CancellationToken.None);
    Show("Disconnected without deleting the saved secure session.", InfoBarSeverity.Success);
  }

  private async void OnRemoveClick(object sender, RoutedEventArgs args)
  {
    if (RequireSelection() is not { } profile)
    {
      return;
    }

    var confirmation = new ContentDialog
    {
      XamlRoot = XamlRoot,
      Title = "Remove Site profile?",
      Content = $"Remove {profile.DisplayName}? Secure session material will also be deleted. Unfinished transfers prevent removal.",
      PrimaryButtonText = "REMOVE",
      CloseButtonText = "CANCEL",
      DefaultButton = ContentDialogButton.Close,
    };
    if (await confirmation.ShowAsync() != ContentDialogResult.Primary)
    {
      return;
    }

    try
    {
      await connections.DeleteProfileAsync(profile.ProfileId, CancellationToken.None);
      await ReloadAsync();
      Show("Site profile removed.", InfoBarSeverity.Success);
    }
    catch (InvalidOperationException exception)
    {
      Show(exception.Message, InfoBarSeverity.Error);
    }
  }

  private async Task ShowEditorAsync(SiteProfile? existing)
  {
    var dialog = new AddSiteDialog(connections, existing) { XamlRoot = XamlRoot };
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
      SiteConnectionStatus status = string.IsNullOrEmpty(dialog.Draft.Password)
          ? await connections.RestoreAsync(profile, CancellationToken.None)
          : await connections.ConnectAsync(
              profile,
              dialog.Draft.Username,
              dialog.Draft.Password,
              CancellationToken.None);
      await ReloadAsync(profile.ProfileId);
      Show(
          status.Message,
          status.State == SiteConnectionState.Connected ? InfoBarSeverity.Success : InfoBarSeverity.Error);
    }
    catch (Exception exception)
    {
      Show(exception.Message, InfoBarSeverity.Error);
    }
  }

  private SiteProfile? RequireSelection()
  {
    if (ProfileList.SelectedItem is SiteProfile profile)
    {
      return profile;
    }

    Show("Select a Site profile first.", InfoBarSeverity.Warning);
    return null;
  }

  private async Task ReloadAsync(Guid? selectedId = null)
  {
    var current = selectedId ?? (ProfileList.SelectedItem as SiteProfile)?.ProfileId;
    profiles.Clear();
    foreach (var profile in await store.ListSiteProfilesAsync())
    {
      profiles.Add(profile);
    }

    ProfileList.SelectedItem = profiles.FirstOrDefault(item => item.ProfileId == current);
    EmptyProfiles.Visibility = profiles.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
  }

  private void Show(string message, InfoBarSeverity severity)
  {
    ActionInfo.Message = message;
    ActionInfo.Severity = severity;
    ActionInfo.IsOpen = true;
  }
}
