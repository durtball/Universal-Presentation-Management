using System.Net;
using System.Net.Sockets;
using System.Security.Authentication;
using System.Text.Json;
using System.Collections.Concurrent;
using Microsoft.Extensions.Logging;
using UPM.Windows.Core;

namespace UPM.Windows.SiteApi;

public sealed record SiteOperationalSnapshot(
    SiteRegistration Registration,
    IReadOnlyList<EventDeployment> EventDeployments,
    JsonElement MediaStorage,
    IReadOnlyList<JsonElement> Devices,
    JsonElement OperationsDashboard);

public sealed class SiteConnectionStatus
{
  public Guid ProfileId { get; init; }
  public SiteConnectionState State { get; init; }
  public string Message { get; init; } = string.Empty;
  public string? TechnicalDetail { get; init; }
  public SiteProfile? Profile { get; init; }
  public AuthSession? Session { get; init; }
  public SiteOperationalSnapshot? Snapshot { get; init; }
}

public sealed class SiteConnectionChangedEventArgs(SiteConnectionStatus status) : EventArgs
{
  public SiteConnectionStatus Status { get; } = status;
}

public interface ISiteConnectionManager
{
  event EventHandler<SiteConnectionChangedEventArgs>? ConnectionChanged;
  SiteConnectionStatus? Current { get; }
  SiteConnectionStatus? GetStatus(Guid profileId);
  Task<SiteConnectionStatus> TestAsync(SiteProfile profile, CancellationToken cancellationToken);
  Task<SiteConnectionStatus> ConnectAsync(SiteProfile profile, string username, string password, CancellationToken cancellationToken);
  Task<SiteConnectionStatus> RestoreAsync(SiteProfile profile, CancellationToken cancellationToken);
  Task<SiteOperationalSnapshot> RefreshAsync(Guid profileId, CancellationToken cancellationToken);
  Task SelectEventAsync(Guid profileId, Guid? eventId, CancellationToken cancellationToken);
  Task LogoutAsync(Guid profileId, CancellationToken cancellationToken);
  Task DisconnectAsync(Guid profileId, CancellationToken cancellationToken);
  Task DeleteProfileAsync(Guid profileId, CancellationToken cancellationToken);
}

public sealed class SiteConnectionManager(
    LocalStateStore store,
    ICredentialVault credentials,
    ISiteClientFactory clients,
    ILogger<SiteConnectionManager> logger) : ISiteConnectionManager
{
  private readonly ConcurrentDictionary<Guid, SiteConnectionStatus> statuses = new();

  public event EventHandler<SiteConnectionChangedEventArgs>? ConnectionChanged;
  public SiteConnectionStatus? Current { get; private set; }

  public SiteConnectionStatus? GetStatus(Guid profileId) =>
      statuses.GetValueOrDefault(profileId);

  public async Task<SiteConnectionStatus> TestAsync(
      SiteProfile profile,
      CancellationToken cancellationToken)
  {
    try
    {
      await clients.GetClient(profile).Api.GetHealthAsync(cancellationToken);
      var status = CreateStatus(profile, SiteConnectionState.Reachable, "UPM Site is reachable.");
      statuses[profile.ProfileId] = status;
      return status;
    }
    catch (Exception exception)
    {
      var (state, message) = Classify(exception);
      logger.LogWarning(
          exception,
          "Site connection test failed for profile {ProfileId}: {FailureState}",
          profile.ProfileId,
          state);
      var status = CreateStatus(profile, state, message, technicalDetail: exception.ToString());
      statuses[profile.ProfileId] = status;
      return status;
    }
  }

  public async Task<SiteConnectionStatus> ConnectAsync(
      SiteProfile profile,
      string username,
      string password,
      CancellationToken cancellationToken)
  {
    Publish(profile, SiteConnectionState.Connecting, "Connecting to Site…");
    try
    {
      var client = clients.GetClient(profile).Api;
      await client.GetHealthAsync(cancellationToken);
      Publish(profile, SiteConnectionState.Authenticating, "Authenticating…");
      var session = await client.LoginAsync(username, password, cancellationToken);
      return await FinishConnectionAsync(profile, session, cancellationToken);
    }
    catch (Exception exception)
    {
      return PublishFailure(profile, exception);
    }
  }

  public async Task<SiteConnectionStatus> RestoreAsync(
      SiteProfile profile,
      CancellationToken cancellationToken)
  {
    Publish(profile, SiteConnectionState.Connecting, "Restoring secure Site session…");
    var token = await credentials.ReadAsync(profile.ProfileId, cancellationToken);
    if (string.IsNullOrWhiteSpace(token))
    {
      return Publish(profile, SiteConnectionState.AuthenticationRequired, "Authentication required.");
    }

    try
    {
      var client = clients.GetClient(profile).Api;
      await client.GetHealthAsync(cancellationToken);
      client.RestoreSessionCookie(token);
      var session = await client.RestoreSessionAsync(cancellationToken);
      if (session is null)
      {
        await credentials.ForgetAsync(profile.ProfileId, cancellationToken);
        return Publish(profile, SiteConnectionState.SessionExpired, "The saved Site session expired. Sign in again.");
      }

      return await FinishConnectionAsync(profile, session, cancellationToken);
    }
    catch (Exception exception)
    {
      return PublishFailure(profile, exception);
    }
  }

  public async Task<SiteOperationalSnapshot> RefreshAsync(
      Guid profileId,
      CancellationToken cancellationToken)
  {
    var profile = await RequireProfileAsync(profileId, cancellationToken);
    var api = clients.GetClient(profile).Api;
    try
    {
      var snapshot = await LoadSnapshotAsync(api, cancellationToken);
      Publish(profile, SiteConnectionState.Connected, $"Connected to {snapshot.Registration.DisplayName}.", Current?.Session, snapshot);
      return snapshot;
    }
    catch (Exception exception)
    {
      PublishFailure(profile, exception);
      throw;
    }
  }

  public async Task SelectEventAsync(
      Guid profileId,
      Guid? eventId,
      CancellationToken cancellationToken)
  {
    var profile = await RequireProfileAsync(profileId, cancellationToken);
    var updated = profile with
    {
      LastSelectedEventId = eventId,
      UpdatedAt = DateTimeOffset.UtcNow,
    };
    await store.UpsertSiteProfileAsync(updated, cancellationToken);
  }

  public async Task LogoutAsync(Guid profileId, CancellationToken cancellationToken)
  {
    var profile = await RequireProfileAsync(profileId, cancellationToken);
    try
    {
      await clients.GetClient(profile).Api.LogoutAsync(cancellationToken);
    }
    catch (Exception exception)
    {
      logger.LogWarning(exception, "Site logout request failed for profile {ProfileId}", profileId);
    }
    finally
    {
      await credentials.ForgetAsync(profileId, cancellationToken);
      clients.Remove(profileId);
      Publish(profile, SiteConnectionState.AuthenticationRequired, "Logged out. Authentication required.");
    }
  }

  public async Task DisconnectAsync(Guid profileId, CancellationToken cancellationToken)
  {
    var profile = await RequireProfileAsync(profileId, cancellationToken);
    clients.Remove(profileId);
    Publish(profile, SiteConnectionState.Disconnected, "Disconnected.");
  }

  public async Task DeleteProfileAsync(Guid profileId, CancellationToken cancellationToken)
  {
    await store.DeleteSiteProfileAsync(profileId, cancellationToken);
    await credentials.ForgetAsync(profileId, cancellationToken);
    clients.Remove(profileId);
    if (Current?.ProfileId == profileId)
    {
      Publish(null, SiteConnectionState.Disconnected, "Site profile removed.");
    }
  }

  private async Task<SiteConnectionStatus> FinishConnectionAsync(
      SiteProfile profile,
      AuthSession session,
      CancellationToken cancellationToken)
  {
    var client = clients.GetClient(profile).Api;
    var registration = await client.GetRegistrationAsync(cancellationToken);
    if (profile.CanonicalSiteId.HasValue && profile.CanonicalSiteId != registration.SiteId)
    {
      client.ClearSession();
      await credentials.ForgetAsync(profile.ProfileId, cancellationToken);
      return Publish(
          profile,
          SiteConnectionState.IdentityMismatch,
          $"Site identity mismatch. Expected {profile.CanonicalSiteId}, received {registration.SiteId}.",
          session);
    }

    var snapshot = await LoadSnapshotAsync(client, registration, cancellationToken);

    var cookie = client.GetSessionCookie();
    if (string.IsNullOrWhiteSpace(cookie))
    {
      throw new InvalidDataException("Site authenticated without returning a session cookie.");
    }

    await credentials.SaveAsync(profile.ProfileId, cookie, cancellationToken);
    var now = DateTimeOffset.UtcNow;
    var updated = profile with
    {
      RememberedUsername = session.User.Username,
      CanonicalSiteId = snapshot.Registration.SiteId,
      CanonicalSiteDisplayName = snapshot.Registration.DisplayName,
      UpdatedAt = now,
      LastConnectedAt = now,
      LastSelectedEventId = snapshot.EventDeployments.Any(item => item.EventId == profile.LastSelectedEventId)
            ? profile.LastSelectedEventId
            : null,
    };
    await store.UpsertSiteProfileAsync(updated, cancellationToken);
    await store.SetPreferenceAsync("last_site_profile_id", updated.ProfileId.ToString(), cancellationToken);
    return Publish(
        updated,
        SiteConnectionState.Connected,
        $"Connected to {snapshot.Registration.DisplayName}.",
        session,
        snapshot);
  }

  private static async Task<SiteOperationalSnapshot> LoadSnapshotAsync(
      SiteApiClient api,
      CancellationToken cancellationToken)
  {
    var registration = await api.GetRegistrationAsync(cancellationToken);
    return await LoadSnapshotAsync(api, registration, cancellationToken);
  }

  private static async Task<SiteOperationalSnapshot> LoadSnapshotAsync(
      SiteApiClient api,
      SiteRegistration registration,
      CancellationToken cancellationToken)
  {
    var deployments = await api.GetEventDeploymentsAsync(cancellationToken);
    var storage = await api.GetMediaStorageAsync(cancellationToken);
    var devices = await api.GetDevicesAsync(cancellationToken);
    var dashboard = await api.GetOperationsDashboardAsync(cancellationToken);
    return new SiteOperationalSnapshot(registration, deployments, storage, devices, dashboard);
  }

  private async Task<SiteProfile> RequireProfileAsync(Guid profileId, CancellationToken cancellationToken) =>
      await store.GetSiteProfileAsync(profileId, cancellationToken)
      ?? throw new KeyNotFoundException($"Site profile {profileId} does not exist.");

  private SiteConnectionStatus Publish(
    SiteProfile? profile,
    SiteConnectionState state,
    string message,
    AuthSession? session = null,
    SiteOperationalSnapshot? snapshot = null,
    string? technicalDetail = null)
  {
    Current = CreateStatus(profile, state, message, session, snapshot, technicalDetail);
    if (profile is not null)
    {
      statuses[profile.ProfileId] = Current;
    }
    ConnectionChanged?.Invoke(this, new SiteConnectionChangedEventArgs(Current));
    return Current;
  }

  private static SiteConnectionStatus CreateStatus(
      SiteProfile? profile,
      SiteConnectionState state,
      string message,
      AuthSession? session = null,
      SiteOperationalSnapshot? snapshot = null,
      string? technicalDetail = null) => new()
      {
        ProfileId = profile?.ProfileId ?? Guid.Empty,
        Profile = profile,
        State = state,
        Message = message,
        TechnicalDetail = technicalDetail,
        Session = session,
        Snapshot = snapshot,
      };

  private SiteConnectionStatus PublishFailure(SiteProfile profile, Exception exception)
  {
    var (state, message) = Classify(exception);
    logger.LogWarning(exception, "Site connection failed for profile {ProfileId}: {FailureState}", profile.ProfileId, state);
    return Publish(profile, state, message, technicalDetail: exception.ToString());
  }

  public static (SiteConnectionState State, string Message) Classify(Exception exception) => exception switch
  {
    SiteEndpointException endpoint => (endpoint.State, endpoint.Message),
    OperationCanceledException => (SiteConnectionState.Unreachable, "The Site connection timed out."),
    HttpRequestException { InnerException: AuthenticationException } =>
        (SiteConnectionState.Unreachable, "TLS certificate validation failed."),
    HttpRequestException { InnerException: SocketException { SocketErrorCode: SocketError.HostNotFound } } =>
        (SiteConnectionState.Unreachable, "The Site host name could not be resolved."),
    HttpRequestException { InnerException: SocketException { SocketErrorCode: SocketError.ConnectionRefused } } =>
        (SiteConnectionState.Unreachable, "The Site refused the connection."),
    HttpRequestException => (SiteConnectionState.Unreachable, "The Site could not be reached."),
    JsonException or InvalidDataException => (SiteConnectionState.Error, "The Site returned an invalid response."),
    _ => (SiteConnectionState.Error, "An unexpected Site connection error occurred."),
  };
}

public sealed record SiteTransferDestination(SiteProfile Profile, Guid CanonicalSiteId, SiteApiClient Api);

public interface ISiteTransferRouter
{
  Task<SiteTransferDestination> ResolveAsync(Guid profileId, CancellationToken cancellationToken);
}

public sealed class SiteTransferRouter(
    LocalStateStore store,
    ICredentialVault credentials,
    ISiteClientFactory clients) : ISiteTransferRouter
{
  public async Task<SiteTransferDestination> ResolveAsync(
      Guid profileId,
      CancellationToken cancellationToken)
  {
    var profile = await store.GetSiteProfileAsync(profileId, cancellationToken)
        ?? throw new InvalidOperationException("The transfer's Site profile no longer exists.");
    if (!profile.CanonicalSiteId.HasValue)
    {
      throw new InvalidOperationException("The transfer's Site profile has no verified canonical Site identity.");
    }

    var api = clients.GetClient(profile).Api;
    if (string.IsNullOrWhiteSpace(api.CsrfToken))
    {
      var token = await credentials.ReadAsync(profileId, cancellationToken)
          ?? throw new InvalidOperationException("The transfer's Site session requires authentication.");
      api.RestoreSessionCookie(token);
      if (await api.RestoreSessionAsync(cancellationToken) is null)
      {
        await credentials.ForgetAsync(profileId, cancellationToken);
        throw new InvalidOperationException("The transfer's Site session expired.");
      }
    }

    var registration = await api.GetRegistrationAsync(cancellationToken);
    if (registration.SiteId != profile.CanonicalSiteId)
    {
      throw new SiteEndpointException(
          $"Site identity mismatch for transfer destination. Expected {profile.CanonicalSiteId}, received {registration.SiteId}.",
          SiteConnectionState.IdentityMismatch);
    }

    return new SiteTransferDestination(profile, registration.SiteId, api);
  }
}
