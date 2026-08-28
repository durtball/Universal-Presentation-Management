using Microsoft.UI.Xaml.Controls;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;

namespace UPM.SiteManager;

public sealed record SiteProfileDraft(
    Guid ProfileId,
    string DisplayName,
    Uri BaseUri,
    string Username,
    string Password,
    SiteProfile? ExistingProfile);

public sealed partial class AddSiteDialog : ContentDialog
{
  private readonly ISiteConnectionManager connectionManager;
  private readonly SiteProfile? existing;

  public AddSiteDialog(ISiteConnectionManager connectionManager, SiteProfile? existing = null)
  {
    this.connectionManager = connectionManager;
    this.existing = existing;
    InitializeComponent();
    if (existing is not null)
    {
      Title = "Edit UPM Site";
      DisplayNameBox.Text = existing.DisplayName;
      AddressBox.Text = existing.BaseUri.AbsoluteUri;
      UsernameBox.Text = existing.RememberedUsername ?? string.Empty;
    }
  }

  public SiteProfileDraft? Draft { get; private set; }

  private void OnPrimaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
  {
    try
    {
      Draft = BuildDraft(requireUsername: true, requirePassword: existing is null);
    }
    catch (FormatException exception)
    {
      args.Cancel = true;
      ShowValidation(exception.Message, InfoBarSeverity.Error);
    }
  }

  private async void OnSecondaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
  {
    args.Cancel = true;
    try
    {
      var draft = BuildDraft(requireUsername: false, requirePassword: false);
      var profile = CreateProfile(draft);
      IsPrimaryButtonEnabled = false;
      IsSecondaryButtonEnabled = false;
      var status = await connectionManager.TestAsync(profile, CancellationToken.None);
      ShowValidation(
          status.Message,
          status.State == SiteConnectionState.Reachable
              ? InfoBarSeverity.Success
              : InfoBarSeverity.Error);
    }
    catch (FormatException exception)
    {
      ShowValidation(exception.Message, InfoBarSeverity.Error);
    }
    finally
    {
      IsPrimaryButtonEnabled = true;
      IsSecondaryButtonEnabled = true;
    }
  }

  public static SiteProfile CreateProfile(SiteProfileDraft draft)
  {
    var now = DateTimeOffset.UtcNow;
    return new SiteProfile(
        draft.ProfileId,
        draft.DisplayName,
        draft.BaseUri,
        draft.Username,
        draft.ExistingProfile?.CanonicalSiteId,
        draft.ExistingProfile?.CanonicalSiteDisplayName,
        draft.ExistingProfile?.CertificateThumbprint,
        draft.ExistingProfile?.CreatedAt ?? now,
        now,
        draft.ExistingProfile?.LastConnectedAt,
        draft.ExistingProfile?.LastSelectedEventId);
  }

  private SiteProfileDraft BuildDraft(bool requireUsername, bool requirePassword)
  {
    var displayName = DisplayNameBox.Text.Trim();
    if (displayName.Length == 0)
    {
      throw new FormatException("Enter a display name for this Site.");
    }

    int? port = double.IsNaN(PortBox.Value) ? null : checked((int)PortBox.Value);
    var baseUri = SiteAddressNormalizer.Normalize(AddressBox.Text, port);
    var username = UsernameBox.Text.Trim();
    if (requireUsername && username.Length == 0)
    {
      throw new FormatException("Enter the Site username.");
    }

    if (requirePassword && PasswordBox.Password.Length == 0)
    {
      throw new FormatException("Enter the Site password to connect.");
    }

    return new SiteProfileDraft(
        existing?.ProfileId ?? Guid.CreateVersion7(),
        displayName,
        baseUri,
        username,
        PasswordBox.Password,
        existing);
  }

  private void ShowValidation(string message, InfoBarSeverity severity)
  {
    ValidationInfo.Message = message;
    ValidationInfo.Severity = severity;
    ValidationInfo.IsOpen = true;
  }
}
