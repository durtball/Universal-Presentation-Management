using System.Text;
using UPM.Signage;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class SignageDiscoveryTests
{
  [Fact]
  public void ParsesFriendlyHttpsAdvertisement()
  {
    var item = SignageDiscoveryService.Parse(Encoding.UTF8.GetBytes("""
      {"installation_id":"019c1111-1111-7111-8111-111111111111","site_name":"MGM Grand Las Vegas","hostname":"ms01","endpoint":"https://ms01:8445/","version":"0.2.0","product":"UPM Signage"}
      """));
    Assert.NotNull(item);
    Assert.Equal("MGM Grand Las Vegas", item.SiteName);
    Assert.Equal("ms01", item.Hostname);
    Assert.Equal("https://ms01:8445/", item.Endpoint.AbsoluteUri);
  }

  [Fact]
  public void RejectsMalformedAdvertisement() =>
    Assert.Throws<System.Text.Json.JsonException>(() =>
      SignageDiscoveryService.Parse(Encoding.UTF8.GetBytes("not-json")));
}
