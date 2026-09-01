using System.Globalization;

namespace UPM.Windows.Agent;

public sealed class PresentationLibrary
{
  public PresentationLibrary(string root)
  {
    try
    {
      Root = Path.GetFullPath(root);
    }
    catch (Exception exception) when (exception is ArgumentException or NotSupportedException or PathTooLongException)
    {
      throw new IOException($"'{root}' is not a valid presentation library path.", exception);
    }
  }

  public string Root { get; }

  public void EnsureRoot()
  {
    try
    {
      Directory.CreateDirectory(Root);
    }
    catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or NotSupportedException)
    {
      throw new IOException($"UPM could not create or access the presentation library at '{Root}'.", exception);
    }
  }

  public string DayPath(DateOnly day) => WindowsPathPolicy.EnsureContained(
      Root, $"{day:yyyy-MM-dd} - {day.ToString("dddd", CultureInfo.InvariantCulture)}");

  public string SessionPath(AgentSession session)
  {
    var day = DayPath(DateOnly.FromDateTime(session.StartsAt.LocalDateTime));
    var room = WindowsPathPolicy.SanitizeSegment(session.RoomName);
    var title = WindowsPathPolicy.SanitizeSegment(session.Title, maxLength: 80);
    var identifier = string.IsNullOrWhiteSpace(session.SessionIdentifier)
        ? string.Empty : $" - {WindowsPathPolicy.SanitizeSegment(session.SessionIdentifier, maxLength: 40)}";
    var folder = $"{session.StartsAt.LocalDateTime:hh-mm tt} - {title}{identifier}";
    if (session.Cancelled) folder = Path.Combine("_Cancelled", folder);
    return WindowsPathPolicy.EnsureContained(day, room, folder);
  }

  public string RotationPath(DateOnly day, RotationScope scope, AgentSession? session = null, string? room = null) => scope switch
  {
    RotationScope.Day => WindowsPathPolicy.EnsureContained(DayPath(day), "Rotating Slides"),
    RotationScope.Room => WindowsPathPolicy.EnsureContained(DayPath(day), WindowsPathPolicy.SanitizeSegment(room ?? session?.RoomName ?? throw new ArgumentException("Room is required.")), "Rotating Slides"),
    RotationScope.Session when session is not null => WindowsPathPolicy.EnsureContained(SessionPath(session), "Rotating Slides"),
    _ => throw new ArgumentException("A session is required for a session override."),
  };

  public async Task<string> PublishAsync(AgentAsset asset, AgentSession? session, CancellationToken ct = default)
  {
    if (!asset.Verified || !File.Exists(asset.ManagedPath)) throw new InvalidOperationException("Only a verified managed asset can be published.");
    EnsureRoot();
    string directory;
    if (asset.Kind == AssetKind.RotatingSlide)
    {
      var day = asset.EventDay ?? throw new InvalidOperationException("Rotating slides require an event day.");
      directory = RotationPath(day, asset.RotationScope ?? RotationScope.Day, session, session?.RoomName);
    }
    else
    {
      directory = SessionPath(session ?? throw new InvalidOperationException("Presentation assets require a session."));
    }

    Directory.CreateDirectory(directory);
    var filename = WindowsPathPolicy.UploadedFilename(asset.OriginalFilename);
    var destination = UniqueDestination(directory, filename, asset.ManagedPath);
    var temporary = destination + ".upm-new";
    await using (var source = new FileStream(asset.ManagedPath, FileMode.Open, FileAccess.Read, FileShare.Read, 1024 * 1024, true))
    await using (var output = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None, 1024 * 1024, true))
      await source.CopyToAsync(output, ct);
    File.Move(temporary, destination, true);
    return destination;
  }

  private static string UniqueDestination(string directory, string filename, string source)
  {
    var candidate = Path.Combine(directory, filename);
    if (!File.Exists(candidate) || FilesEqual(candidate, source)) return candidate;
    var stem = Path.GetFileNameWithoutExtension(filename); var extension = Path.GetExtension(filename);
    for (var index = 2; ; index++)
    {
      candidate = Path.Combine(directory, $"{stem} ({index}){extension}");
      if (!File.Exists(candidate) || FilesEqual(candidate, source)) return candidate;
    }
  }

  private static bool FilesEqual(string first, string second)
  {
    if (new FileInfo(first).Length != new FileInfo(second).Length) return false;
    using var left = File.OpenRead(first); using var right = File.OpenRead(second);
    var leftBuffer = new byte[64 * 1024]; var rightBuffer = new byte[64 * 1024];
    while (true)
    {
      var leftRead = left.Read(leftBuffer); var rightRead = right.Read(rightBuffer);
      if (leftRead != rightRead || !leftBuffer[..leftRead].SequenceEqual(rightBuffer[..rightRead])) return false;
      if (leftRead == 0) return true;
    }
  }
}
