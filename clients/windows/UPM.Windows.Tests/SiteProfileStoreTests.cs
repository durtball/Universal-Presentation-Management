using Microsoft.Data.Sqlite;
using UPM.Windows.Core;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class SiteProfileStoreTests
{
  [Fact]
  public async Task SiteProfileCrudPersistsOnlyNonSecretState()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      var databasePath = Path.Combine(root.FullName, "state.db");
      var store = new LocalStateStore(databasePath);
      await store.InitializeAsync();
      var now = DateTimeOffset.UtcNow;
      var profile = new SiteProfile(
          Guid.NewGuid(),
          "Main Site",
          new Uri("http://site.local:9080/"),
          "operator",
          Guid.NewGuid(),
          "Canonical Site",
          null,
          now,
          now,
          now,
          Guid.NewGuid());

      await store.UpsertSiteProfileAsync(profile);
      var loaded = await store.GetSiteProfileAsync(profile.ProfileId);
      var listed = await store.ListSiteProfilesAsync();

      Assert.Equal(profile, loaded);
      Assert.Equal(profile, Assert.Single(listed));

      await using var database = new SqliteConnection($"Data Source={databasePath}");
      await database.OpenAsync();
      var schema = database.CreateCommand();
      schema.CommandText = "SELECT sql FROM sqlite_master WHERE name='site_profiles'";
      var definition = Assert.IsType<string>(await schema.ExecuteScalarAsync());
      Assert.DoesNotContain("password", definition, StringComparison.OrdinalIgnoreCase);
      Assert.DoesNotContain("session", definition, StringComparison.OrdinalIgnoreCase);
      Assert.DoesNotContain("csrf", definition, StringComparison.OrdinalIgnoreCase);

      await store.DeleteSiteProfileAsync(profile.ProfileId);
      Assert.Null(await store.GetSiteProfileAsync(profile.ProfileId));
    }
    finally
    {
      root.Delete(true);
    }
  }
}
