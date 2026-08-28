using Microsoft.Extensions.Logging;

namespace UPM.SiteManager;

public sealed class LocalFileLoggerProvider(string applicationRoot) : ILoggerProvider
{
  private readonly object sync = new();
  private readonly string logDirectory = Path.Combine(applicationRoot, "logs");

  public ILogger CreateLogger(string categoryName) => new LocalFileLogger(this, categoryName);

  public void Dispose()
  {
  }

  private void Write(string category, LogLevel level, string message, Exception? exception)
  {
    Directory.CreateDirectory(logDirectory);
    var path = Path.Combine(logDirectory, $"site-manager-{DateTimeOffset.UtcNow:yyyyMMdd}.log");
    var entry = $"[{DateTimeOffset.UtcNow:O}] {level} {category}: {message}{Environment.NewLine}";
    if (exception is not null)
    {
      entry += exception + Environment.NewLine;
    }

    lock (sync)
    {
      File.AppendAllText(path, entry);
    }
  }

  private sealed class LocalFileLogger(
      LocalFileLoggerProvider provider,
      string category) : ILogger
  {
    public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;

    public bool IsEnabled(LogLevel logLevel) => logLevel >= LogLevel.Information;

    public void Log<TState>(
        LogLevel logLevel,
        EventId eventId,
        TState state,
        Exception? exception,
        Func<TState, Exception?, string> formatter)
    {
      if (IsEnabled(logLevel))
      {
        provider.Write(category, logLevel, formatter(state, exception), exception);
      }
    }
  }
}
