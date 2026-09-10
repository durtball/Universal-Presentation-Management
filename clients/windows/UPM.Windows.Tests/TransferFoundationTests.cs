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

  [Fact]
  public async Task DurableByteReceiptIsNotReplayedAfterRestart()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      var path = Path.Combine(root.FullName, "state.db");
      var store = new LocalStateStore(path); await store.InitializeAsync();
      var receiptId = Guid.NewGuid();
      var item = new TransferItem(Guid.NewGuid(), Guid.NewGuid(), Guid.NewGuid(), "deck.pptx",
          "deck.pptx", Path.Combine("room", "deck.pptx"), null, 3, DateTimeOffset.UtcNow, "receipt-key");
      await store.EnqueueAsync(item);
      await store.UpdateAsync(item.TransferId, TransferState.ReceivedBySite, 3, new string('a', 64),
          receiptId: receiptId);

      var reopened = new LocalStateStore(path); await reopened.InitializeAsync();
      var pending = new List<TransferItem>();
      await foreach (var row in reopened.LoadPendingAsync()) pending.Add(row);

      Assert.Empty(pending);
      var saved = Assert.Single(await reopened.ListTransfersAsync());
      Assert.Equal(receiptId, saved.ReceiptId);
      Assert.Equal(TransferState.ReceivedBySite, saved.State);
    }
    finally { root.Delete(true); }
  }

  [Fact]
  public async Task FutureRetryIsDurablyScheduledRatherThanImmediatelyLoaded()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      var store = new LocalStateStore(Path.Combine(root.FullName, "state.db")); await store.InitializeAsync();
      var item = new TransferItem(Guid.NewGuid(), Guid.NewGuid(), null, "deck.pptx", "deck.pptx",
          "deck.pptx", null, 3, DateTimeOffset.UtcNow, "due-key");
      await store.EnqueueAsync(item);
      await store.UpdateAsync(item.TransferId, TransferState.RetryWaiting, retry: 1,
          retryAt: DateTimeOffset.UtcNow.AddMinutes(1), error: "temporary");
      var pending = new List<TransferItem>();
      await foreach (var row in store.LoadPendingAsync()) pending.Add(row);
      Assert.Empty(pending);
    }
    finally { root.Delete(true); }
  }

  [Fact]
  public async Task DispatcherClaimFindsWorkEnqueuedAfterAnEmptyPoll()
  {
    var root = Directory.CreateTempSubdirectory();
    try
    {
      var store = new LocalStateStore(Path.Combine(root.FullName, "state.db")); await store.InitializeAsync();
      Assert.Null(await store.ClaimDueAsync());
      var item = new TransferItem(Guid.NewGuid(), Guid.NewGuid(), null, "deck.pptx", "deck.pptx",
          "deck.pptx", null, 3, DateTimeOffset.UtcNow, "after-start-key");
      await store.EnqueueAsync(item);
      var claimed = await store.ClaimDueAsync();
      Assert.Equal(item.TransferId, claimed!.TransferId);
      Assert.Equal(TransferState.Hashing, claimed.State);
    }
    finally { root.Delete(true); }
  }
}
