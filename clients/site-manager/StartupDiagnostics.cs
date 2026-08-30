using System.Text;

namespace UPM.SiteManager;

public sealed class StartupDiagnostics
{
  private readonly string logDirectory;
  private readonly object sync = new();
  private string phase = "application construction";

  public StartupDiagnostics(string applicationRoot)
  {
    logDirectory = Path.Combine(applicationRoot, "logs");
  }

  public string Phase
  {
    get { lock (sync) return phase; }
  }

  public void EnterPhase(string value)
  {
    lock (sync) phase = value;
  }

  public void WriteFailure(string stage, Exception exception)
  {
    try
    {
      Directory.CreateDirectory(logDirectory);
      var path = Path.Combine(logDirectory, $"startup-{DateTimeOffset.UtcNow:yyyyMMdd}.log");
      var entry = new StringBuilder()
          .AppendLine($"[{DateTimeOffset.UtcNow:O}] Startup phase: {stage}")
          .AppendLine($"Exception type: {exception.GetType().FullName}")
          .AppendLine($"Message: {exception.Message}")
          .AppendLine("Stack trace:")
          .AppendLine(exception.StackTrace ?? "<no managed stack trace>");
      var inner = exception.InnerException;
      while (inner is not null)
      {
        entry.AppendLine("INNER EXCEPTION:").AppendLine(inner.ToString());
        inner = inner.InnerException;
      }

      entry.AppendLine();
      lock (sync) File.AppendAllText(path, entry.ToString());
    }
    catch
    {
      // Diagnostics must never replace the original fatal exception.
    }
  }
}
