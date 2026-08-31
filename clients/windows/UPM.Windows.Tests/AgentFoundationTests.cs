using System.Security.Cryptography;
using System.Text;
using UPM.Windows.Agent;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class AgentFoundationTests
{
  [Fact]
  public async Task ProvisioningSessionsOriginalNameAndRevisionsSurviveRestart()
  {
    using var root = Temp(); var path = Path.Combine(root.Path, "agent.db");
    var store = new AgentStateStore(path); await store.InitializeAsync();
    var provision = new ProvisioningState(Guid.NewGuid(), Guid.NewGuid(), "UPM-ROOM-104", Guid.NewGuid(), new Uri("https://site.test"), Guid.NewGuid(), DeviceRole.RoomAgent | DeviceRole.UploadKiosk, Guid.NewGuid(), "Venetian G", DateTimeOffset.UtcNow);
    var session = Session(identifier: "3489435");
    await store.SaveProvisioningAsync(provision); await store.SaveRevisionsAsync(new(4, 5, 6, 7)); await store.UpsertSessionAsync(session);
    var reopened = new AgentStateStore(path); await reopened.InitializeAsync();
    Assert.Equal(AgentStateStore.SchemaVersion, await reopened.GetSchemaVersionAsync());
    Assert.Equal(provision, await reopened.GetProvisioningAsync());
    Assert.Equal("3489435", Assert.Single(await reopened.ListSessionsAsync()).SessionIdentifier);
    Assert.Equal(new SyncRevisions(4, 5, 6, 7), await reopened.GetRevisionsAsync());
  }

  [Theory]
  [InlineData("CON.pptx", "_CON.pptx")]
  [InlineData("Final: Deck?.pptx", "Final_ Deck_.pptx")]
  [InlineData("Deck.pptx ", "Deck.pptx")]
  public void WindowsFilenameSafetyIsMinimalAndDeterministic(string input, string expected) =>
      Assert.Equal(expected, WindowsPathPolicy.UploadedFilename(input));

  [Fact]
  public void UploadedFilenameRejectsTraversal() =>
      Assert.Throws<ArgumentException>(() => WindowsPathPolicy.UploadedFilename("../deck.pptx"));

  [Fact]
  public void LibraryUsesDateRoomTimeTitleAndHumanIdentifier()
  {
    using var root = Temp(); var library = new PresentationLibrary(root.Path);
    var path = library.SessionPath(Session(identifier: "3489435"));
    Assert.EndsWith(Path.Combine("2026-08-31 - Monday", "Venetian G", "10-15 AM - Agentic Model Risk Management - 3489435"), path);
  }

  [Fact]
  public async Task VerifiedDownloadPreservesFilenameAndAtomicActivation()
  {
    using var root = Temp(); var storage = new AgentStorage(root.Path); var bytes = Encoding.UTF8.GetBytes("valid deck");
    var hash = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    var engine = new VerifiedTransferEngine(storage); var name = "Agentic Model Risk Management - FINAL v3.pptx";
    var final = await engine.DownloadAsync(new(Guid.NewGuid(), name, hash, bytes.Length), new MemoryStream(bytes));
    Assert.Equal(name, Path.GetFileName(final)); Assert.Equal(bytes, await File.ReadAllBytesAsync(final));
    Assert.Empty(Directory.EnumerateFiles(storage.Downloads, "*.partial"));
  }

  [Fact]
  public async Task HashMismatchKeepsNoPartialOrActivatedFile()
  {
    using var root = Temp(); var storage = new AgentStorage(root.Path); var engine = new VerifiedTransferEngine(storage);
    await Assert.ThrowsAsync<InvalidDataException>(() => engine.DownloadAsync(new(Guid.NewGuid(), "deck.pptx", new string('0', 64), 3), new MemoryStream([1, 2, 3])));
    Assert.Empty(Directory.EnumerateFiles(storage.Downloads));
  }

  [Fact]
  public async Task OfflineIntakeCopiesAndQueuesWithoutDependingOnSource()
  {
    using var root = Temp(); var storage = new AgentStorage(Path.Combine(root.Path, "managed")); storage.EnsureCreated();
    var store = new AgentStateStore(storage.DatabasePath); await store.InitializeAsync();
    var source = Path.Combine(root.Path, "Presenter FINAL.pptx"); await File.WriteAllTextAsync(source, "replacement");
    var change = await new LocalIntakeService(storage, store).IngestAsync(Guid.NewGuid(), Guid.NewGuid(), null, source);
    File.Delete(source);
    Assert.Equal("Presenter FINAL.pptx", change.OriginalFilename); Assert.True(File.Exists(change.ManagedPath)); Assert.Equal(LocalChangeState.Pending, change.State);
  }

  [Fact]
  public async Task RotatingSlideHierarchyAndDuplicateNamesAreSafe()
  {
    using var root = Temp(); var managed = Path.Combine(root.Path, "cache"); Directory.CreateDirectory(managed);
    var first = Path.Combine(managed, "one.pptx"); var second = Path.Combine(managed, "two.pptx"); await File.WriteAllTextAsync(first, "one"); await File.WriteAllTextAsync(second, "two");
    var library = new PresentationLibrary(Path.Combine(root.Path, "library")); var day = new DateOnly(2026, 8, 31);
    var one = Asset(Guid.NewGuid(), first, "Loop.pptx", day, RotationScope.Room); var two = Asset(Guid.NewGuid(), second, "Loop.pptx", day, RotationScope.Room);
    var session = Session();
    var path1 = await library.PublishAsync(one, session); var path2 = await library.PublishAsync(two, session);
    Assert.EndsWith(Path.Combine("Venetian G", "Rotating Slides", "Loop.pptx"), path1);
    Assert.EndsWith("Loop (2).pptx", path2);
  }

  private static AgentSession Session(string? identifier = "3489435") => new(Guid.NewGuid(), identifier, "Agentic Model Risk Management", "Presenter", Guid.NewGuid(), "Venetian G", new DateTimeOffset(2026, 8, 31, 10, 15, 0, TimeSpan.Zero), new DateTimeOffset(2026, 8, 31, 11, 0, 0, TimeSpan.Zero), false, 1);
  private static AgentAsset Asset(Guid id, string path, string name, DateOnly day, RotationScope scope) => new(id, AssetKind.RotatingSlide, id, null, null, null, day, scope, name, name, "hash", new FileInfo(path).Length, path, true, true, DateTimeOffset.UtcNow);
  private static TemporaryDirectory Temp() => new();
  private sealed class TemporaryDirectory : IDisposable { public TemporaryDirectory() => Path = Directory.CreateTempSubdirectory().FullName; public string Path { get; } public void Dispose() => Directory.Delete(Path, true); }
}
