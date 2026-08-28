using UPM.Windows.Core;
using Windows.Security.Credentials;

namespace UPM.SiteManager;

public sealed class CredentialVault : ICredentialVault
{
  private const string Resource = "UPM.SiteManager.Session";

  public ValueTask SaveAsync(Guid profileId, string sessionCookie, CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    var vault = new PasswordVault();
    Forget(vault, profileId);
    vault.Add(new PasswordCredential(Resource, profileId.ToString("N"), sessionCookie));
    return ValueTask.CompletedTask;
  }

  public ValueTask<string?> ReadAsync(Guid profileId, CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    try
    {
      var credential = new PasswordVault().Retrieve(Resource, profileId.ToString("N"));
      credential.RetrievePassword();
      return ValueTask.FromResult<string?>(credential.Password);
    }
    catch (Exception) when (OperatingSystem.IsWindows())
    {
      return ValueTask.FromResult<string?>(null);
    }
  }

  public ValueTask ForgetAsync(Guid profileId, CancellationToken cancellationToken)
  {
    cancellationToken.ThrowIfCancellationRequested();
    Forget(new PasswordVault(), profileId);
    return ValueTask.CompletedTask;
  }

  private static void Forget(PasswordVault vault, Guid profileId)
  {
    try
    {
      vault.Remove(vault.Retrieve(Resource, profileId.ToString("N")));
    }
    catch (Exception) when (OperatingSystem.IsWindows())
    {
      // An absent credential is already forgotten.
    }
  }
}
