namespace UPM.Windows.Agent;

public sealed class AgentStorage
{
  public AgentStorage(string root)
  {
    Root = Path.GetFullPath(root);
    Database = Path.Combine(Root, "database");
    Cache = Path.Combine(Root, "cache");
    Downloads = Path.Combine(Root, "downloads");
    Branding = Path.Combine(Root, "branding");
    Kiosk = Path.Combine(Root, "kiosk");
    Pending = Path.Combine(Root, "pending");
    Logs = Path.Combine(Root, "logs");
  }

  public static AgentStorage Default => new(Path.Combine(
      Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "UPM", "Agent"));
  public string Root { get; }
  public string Database { get; }
  public string Cache { get; }
  public string Downloads { get; }
  public string Branding { get; }
  public string Kiosk { get; }
  public string Pending { get; }
  public string Logs { get; }
  public string DatabasePath => Path.Combine(Database, "agent.db");

  public void EnsureCreated()
  {
    foreach (var path in new[] { Root, Database, Cache, Downloads, Branding, Kiosk, Pending, Logs })
      Directory.CreateDirectory(path);
  }
}
