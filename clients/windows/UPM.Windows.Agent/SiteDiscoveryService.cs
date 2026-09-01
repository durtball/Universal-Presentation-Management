using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace UPM.Windows.Agent;

public sealed class SiteDiscoveryService
{
  public const string Probe = "UPM_SITE_DISCOVERY_V1";
  public const int Port = 43820;
  private static readonly IPEndPoint MulticastEndpoint = new(IPAddress.Parse("239.255.77.77"), Port);
  private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web)
  { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

  public async Task<IReadOnlyList<DiscoveredSite>> DiscoverAsync(
      TimeSpan timeout,
      CancellationToken cancellationToken)
  {
    using var udp = new UdpClient(AddressFamily.InterNetwork);
    udp.Client.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
    var probe = Encoding.ASCII.GetBytes(Probe);
    try
    {
      await udp.SendAsync(probe, MulticastEndpoint, cancellationToken);
    }
    catch (SocketException)
    {
      // A disconnected adapter is an expected offline state, not a worker failure.
      return [];
    }
    using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
    deadline.CancelAfter(timeout);
    var sites = new Dictionary<Guid, DiscoveredSite>();
    while (!deadline.IsCancellationRequested)
    {
      try
      {
        var response = await udp.ReceiveAsync(deadline.Token);
        var site = Parse(response.Buffer);
        if (site is not null && site.Endpoint.Scheme is "http" or "https") sites[site.SiteId] = site;
      }
      catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested) { break; }
      catch (SocketException) { break; }
      catch (JsonException) { }
    }
    return sites.Values.OrderBy(site => site.SiteId).ToArray();
  }

  public static DiscoveredSite? Parse(ReadOnlySpan<byte> payload) =>
      JsonSerializer.Deserialize<DiscoveredSite>(payload, Json);
}
