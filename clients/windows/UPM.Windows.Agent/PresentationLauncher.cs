using System.Diagnostics;

namespace UPM.Windows.Agent;

public interface IProcessLauncher { void Launch(ProcessStartInfo startInfo); }
public sealed class ShellProcessLauncher : IProcessLauncher { public void Launch(ProcessStartInfo startInfo) => Process.Start(startInfo); }

public sealed class PresentationLauncher(AgentStateStore state, AgentStorage storage, IProcessLauncher processLauncher)
{
  public async Task LaunchAsync(Guid versionId, CancellationToken ct = default)
  {
    var asset = await state.GetVerifiedVersionAsync(versionId, ct) ?? throw new InvalidOperationException("No verified local copy is ready.");
    var path = Path.GetFullPath(asset.ManagedPath);
    var cache = Path.GetFullPath(storage.Root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
    if (!path.StartsWith(cache, StringComparison.OrdinalIgnoreCase) || path.EndsWith(".partial", StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
      throw new InvalidOperationException("The presentation is not a launchable Agent-managed file.");
    processLauncher.Launch(new ProcessStartInfo(path) { UseShellExecute = true });
  }
}
