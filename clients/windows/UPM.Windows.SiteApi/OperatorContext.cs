using UPM.Windows.Core;

namespace UPM.Windows.SiteApi;

public sealed class OperatorContextChangedEventArgs(SiteConnectionStatus? status, Guid? eventId)
    : EventArgs
{
  public SiteConnectionStatus? Status { get; } = status;
  public Guid? EventId { get; } = eventId;
}

public interface IOperatorContext
{
  event EventHandler<OperatorContextChangedEventArgs>? Changed;
  SiteConnectionStatus? Connection { get; }
  SiteProfile? Profile { get; }
  AuthSession? Session { get; }
  Guid? CanonicalSiteId { get; }
  Guid? SelectedEventId { get; }
  SiteOperationalSnapshot? Snapshot { get; }
  SiteApiClient? ActiveClient { get; }
  void SelectEvent(Guid? eventId);
}

/// <summary>
/// Single application-scoped projection consumed by every operator page. It never owns
/// authentication or an HTTP session; those remain in the one SiteConnectionManager and
/// profile-specific SiteClientFactory instances.
/// </summary>
public sealed class OperatorContext : IOperatorContext, IDisposable
{
  private readonly ISiteConnectionManager connections;
  private readonly ISiteClientFactory clients;

  public OperatorContext(ISiteConnectionManager connections, ISiteClientFactory clients)
  {
    this.connections = connections;
    this.clients = clients;
    Connection = connections.Current;
    SelectedEventId = Connection?.Profile?.LastSelectedEventId;
    connections.ConnectionChanged += OnConnectionChanged;
  }

  public event EventHandler<OperatorContextChangedEventArgs>? Changed;
  public SiteConnectionStatus? Connection { get; private set; }
  public SiteProfile? Profile => Connection?.Profile;
  public AuthSession? Session => Connection?.Session;
  public Guid? CanonicalSiteId => Connection?.Snapshot?.Registration.SiteId;
  public Guid? SelectedEventId { get; private set; }
  public SiteOperationalSnapshot? Snapshot => Connection?.Snapshot;
  public SiteApiClient? ActiveClient =>
      Connection?.State == SiteConnectionState.Connected && Profile is not null
          ? clients.GetClient(Profile).Api
          : null;

  public void SelectEvent(Guid? eventId)
  {
    if (SelectedEventId == eventId)
    {
      return;
    }

    SelectedEventId = eventId;
    Changed?.Invoke(this, new OperatorContextChangedEventArgs(Connection, SelectedEventId));
  }

  public void Dispose() => connections.ConnectionChanged -= OnConnectionChanged;

  private void OnConnectionChanged(object? sender, SiteConnectionChangedEventArgs args)
  {
    var profileChanged = Connection?.ProfileId != args.Status.ProfileId;
    Connection = args.Status;
    if (profileChanged)
    {
      SelectedEventId = args.Status.Profile?.LastSelectedEventId;
    }

    Changed?.Invoke(this, new OperatorContextChangedEventArgs(Connection, SelectedEventId));
  }
}
