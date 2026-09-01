using System.Text;

namespace UPM.Windows.Agent;

public static class WindowsPathPolicy
{
  private static readonly HashSet<string> Reserved = new(StringComparer.OrdinalIgnoreCase)
  {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
  };
  private static readonly HashSet<char> Invalid = ['\\', '/', ':', '*', '?', '"', '<', '>', '|'];

  public static string SanitizeSegment(string value, string fallback = "Untitled", int maxLength = 100)
  {
    ArgumentOutOfRangeException.ThrowIfLessThan(maxLength, 8);
    var builder = new StringBuilder(value.Length);
    foreach (var character in value)
    {
      builder.Append(character < 32 || Invalid.Contains(character) ? '_' : character);
    }

    var result = builder.ToString().TrimEnd(' ', '.');
    if (string.IsNullOrWhiteSpace(result)) result = fallback;
    var stem = Path.GetFileNameWithoutExtension(result);
    if (Reserved.Contains(stem)) result = $"_{result}";
    if (result.Length > maxLength)
    {
      var extension = Path.GetExtension(result);
      var stemLength = Math.Max(1, maxLength - extension.Length);
      result = string.Concat(result.AsSpan(0, stemLength), extension);
    }

    return result;
  }

  public static string UploadedFilename(string original, int maxLength = 180)
  {
    if (string.IsNullOrWhiteSpace(original) || original != Path.GetFileName(original))
      throw new ArgumentException("An uploaded filename must not contain a path.", nameof(original));
    return SanitizeSegment(original, "Presentation", maxLength);
  }

  public static string EnsureContained(string root, params string[] segments)
  {
    var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
    var candidate = Path.GetFullPath(Path.Combine([fullRoot, .. segments]));
    if (!candidate.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase))
      throw new InvalidOperationException("The requested path leaves Agent-managed storage.");
    return candidate;
  }
}
