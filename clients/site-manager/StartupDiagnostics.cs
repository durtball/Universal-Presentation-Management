using System.Text;

namespace UPM.SiteManager;

public sealed class StartupDiagnostics
{
  private readonly string logDirectory;

  public StartupDiagnostics(string applicationRoot)
  {
    logDirectory = Path.Combine(applicationRoot, "logs");
  }

  public void WriteFailure(string stage, Exception exception)
  {
    Directory.CreateDirectory(logDirectory);
    var path = Path.Combine(logDirectory, $"startup-{DateTimeOffset.UtcNow:yyyyMMdd}.log");
    var entry = new StringBuilder()
        .AppendLine($"[{DateTimeOffset.UtcNow:O}] Startup stage: {stage}")
        .AppendLine(exception.ToString());
    var inner = exception.InnerException;
    while (inner is not null)
    {
      entry.AppendLine("INNER EXCEPTION:").AppendLine(inner.ToString());
      inner = inner.InnerException;
    }

    entry.AppendLine();
    File.AppendAllText(path, entry.ToString());
  }
}
