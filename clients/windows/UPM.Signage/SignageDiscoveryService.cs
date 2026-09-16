using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace UPM.Signage;

public sealed record DiscoveredSignage(
  [property: JsonPropertyName("installation_id")] Guid? InstallationId,
  [property: JsonPropertyName("site_name")] string SiteName,
  string Hostname,
  Uri Endpoint,
  string Version,
  string Product);

public sealed class SignageDiscoveryService
{
  internal const string Probe = "UPM_SIGNAGE_DISCOVERY_V1";
  internal const int Port = 43821;
  private static readonly IPEndPoint Multicast = new(IPAddress.Parse("239.255.77.77"), Port);
  private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

  public async Task<IReadOnlyList<DiscoveredSignage>> DiscoverAsync(TimeSpan timeout, CancellationToken ct)
  {
    using var udp = new UdpClient(AddressFamily.InterNetwork);
    udp.Client.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
    try { await udp.SendAsync(Encoding.ASCII.GetBytes(Probe), Multicast, ct); }
    catch (SocketException) { return []; }
    using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
    deadline.CancelAfter(timeout);
    var found = new Dictionary<string, DiscoveredSignage>(StringComparer.OrdinalIgnoreCase);
    while (!deadline.IsCancellationRequested)
    {
      try
      {
        var response = await udp.ReceiveAsync(deadline.Token);
        var item = Parse(response.Buffer);
        if (item is not null && item.Product == "UPM Signage" && item.Endpoint.Scheme == Uri.UriSchemeHttps)
          found[item.Endpoint.AbsoluteUri] = item;
      }
      catch (OperationCanceledException) when (!ct.IsCancellationRequested) { break; }
      catch (SocketException) { break; }
      catch (JsonException) { }
    }
    return found.Values.OrderBy(x => x.SiteName).ThenBy(x => x.Hostname).ToArray();
  }

  public static DiscoveredSignage? Parse(ReadOnlySpan<byte> payload) =>
    JsonSerializer.Deserialize<DiscoveredSignage>(payload, Json);
}
