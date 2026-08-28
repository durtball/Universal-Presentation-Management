using UPM.Windows.Core;
using UPM.Windows.Transfers;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class TransferFoundationTests
{
  [Fact]
  public void IdentityIsStableAndSensitiveToFileRevision()
  {
    var profile = Guid.NewGuid();
    var timestamp = DateTimeOffset.UtcNow;

    Assert.Equal(
        TransferIdentity.Create(profile, null, "a.pptx", 5, timestamp),
        TransferIdentity.Create(profile, null, "a.pptx", 5, timestamp));
    Assert.NotEqual(
        TransferIdentity.Create(profile, null, "a.pptx", 5, timestamp),
        TransferIdentity.Create(profile, null, "a.pptx", 6, timestamp));
  }

  [Fact]
  public async Task DirectoryEnumerationPreservesRelativePathsAndFiltersUnsupportedFiles()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      Directory.CreateDirectory(Path.Combine(root.FullName, "Room A"));
      await File.WriteAllTextAsync(Path.Combine(root.FullName, "Room A", "deck.pptx"), "test");
      await File.WriteAllTextAsync(Path.Combine(root.FullName, "Room A", "notes.txt"), "test");
      var rows = new List<TransferItem>();

      await foreach (var row in IntakeEnumerator.EnumerateAsync(
                         [root.FullName],
                         Guid.NewGuid(),
                         null))
      {
        rows.Add(row);
      }

      Assert.Single(rows);
      Assert.Equal(Path.Combine("Room A", "deck.pptx"), rows[0].RelativePath);
    }
    finally
    {
      root.Delete(true);
    }
  }

  [Fact]
  public async Task QueueSurvivesStoreReopenAndDeduplicates()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      var databasePath = Path.Combine(root.FullName, "state.db");
      var store = new LocalStateStore(databasePath);
      await store.InitializeAsync();
      var timestamp = DateTimeOffset.UtcNow;
      var item = new TransferItem(
          Guid.NewGuid(),
          Guid.NewGuid(),
          null,
          "x.pptx",
          "x.pptx",
          "x.pptx",
          null,
          1,
          timestamp,
          "stable-key");

      await store.EnqueueAsync(item);
      var reopened = new LocalStateStore(databasePath);
      await reopened.InitializeAsync();
      await reopened.EnqueueAsync(item with { TransferId = Guid.NewGuid() });

      var recovered = new List<TransferItem>();
      await foreach (var pending in reopened.LoadPendingAsync())
      {
        recovered.Add(pending);
      }

      Assert.Single(recovered);
      Assert.Equal(item.TransferId, recovered[0].TransferId);
    }
    finally
    {
      root.Delete(true);
    }
  }
}
