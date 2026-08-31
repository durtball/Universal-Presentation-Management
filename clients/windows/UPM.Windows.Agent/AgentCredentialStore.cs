using System.Security.Cryptography;
using System.Text;

namespace UPM.Windows.Agent;

public interface IAgentCredentialStore
{
  Task SaveAsync(string credential, CancellationToken ct = default);
  Task<string?> ReadAsync(CancellationToken ct = default);
  Task ClearAsync(CancellationToken ct = default);
}

public sealed class AgentCredentialStore(AgentStorage storage) : IAgentCredentialStore
{
  private string PathName => Path.Combine(storage.Database, "agent.credential");

  public async Task SaveAsync(string credential, CancellationToken ct = default)
  {
    storage.EnsureCreated();
    var plain = Encoding.UTF8.GetBytes(credential);
    var protectedBytes = OperatingSystem.IsWindows()
        ? ProtectedData.Protect(plain, null, DataProtectionScope.LocalMachine)
        : plain;
    await File.WriteAllBytesAsync(PathName, protectedBytes, ct);
    if (!OperatingSystem.IsWindows()) File.SetUnixFileMode(PathName, UnixFileMode.UserRead | UnixFileMode.UserWrite);
  }

  public async Task<string?> ReadAsync(CancellationToken ct = default)
  {
    if (!File.Exists(PathName)) return null;
    var bytes = await File.ReadAllBytesAsync(PathName, ct);
    var plain = OperatingSystem.IsWindows()
        ? ProtectedData.Unprotect(bytes, null, DataProtectionScope.LocalMachine)
        : bytes;
    return Encoding.UTF8.GetString(plain);
  }

  public Task ClearAsync(CancellationToken ct = default)
  {
    if (File.Exists(PathName)) File.Delete(PathName);
    return Task.CompletedTask;
  }
}
