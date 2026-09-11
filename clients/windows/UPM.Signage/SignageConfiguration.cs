using System.Text.Json;
using System.Security.Cryptography;
using System.Text;
using Windows.Security.Credentials;

namespace UPM.Signage;

internal sealed record SignageConfiguration(
  string ServerUrl = "https://localhost:8445", Guid? DisplayId = null, string DisplayName = "Lobby display",
  int Monitor = 0, bool Fullscreen = true, int Width = 1080, int Height = 1920, bool StartWithWindows = false);

internal sealed class SignageConfigurationStore
{
  private static readonly string DirectoryPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "UPM", "Signage");
  private static readonly string FilePath = Path.Combine(DirectoryPath, "settings.json");
  public static string ManifestPath => Path.Combine(DirectoryPath, "last-verified-manifest.json");
  public async Task<SignageConfiguration> LoadAsync() => File.Exists(FilePath)
    ? JsonSerializer.Deserialize<SignageConfiguration>(await File.ReadAllTextAsync(FilePath)) ?? new()
    : new();
  public async Task SaveAsync(SignageConfiguration value)
  {
    Directory.CreateDirectory(DirectoryPath); var temporary = FilePath + ".new";
    await File.WriteAllTextAsync(temporary, JsonSerializer.Serialize(value)); File.Move(temporary, FilePath, true);
  }
  public static async Task SaveManifestAsync(string json)
  {
    using var document = JsonDocument.Parse(json);
    if (!document.RootElement.TryGetProperty("schema_version", out var version) || version.GetInt32() != 1) throw new InvalidDataException("Unsupported player manifest schema.");
    Directory.CreateDirectory(DirectoryPath); var temporary = ManifestPath + ".new";
    await File.WriteAllTextAsync(temporary, json);
    var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json))).ToLowerInvariant();
    await File.WriteAllTextAsync(temporary + ".sha256", digest);
    File.Move(temporary, ManifestPath, true); File.Move(temporary + ".sha256", ManifestPath + ".sha256", true);
  }
  public static async Task<string?> ReadVerifiedManifestAsync()
  {
    if (!File.Exists(ManifestPath) || !File.Exists(ManifestPath + ".sha256")) return null;
    var json = await File.ReadAllTextAsync(ManifestPath); var expected = await File.ReadAllTextAsync(ManifestPath + ".sha256");
    var actual = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(json))).ToLowerInvariant();
    return CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(actual), Encoding.ASCII.GetBytes(expected.Trim())) ? json : null;
  }
}

internal static class DisplayCredentialStore
{
  private const string Resource = "UPM.Signage.Display";
  public static void Save(Guid displayId, string credential)
  {
    var vault = new PasswordVault();
    foreach (var old in vault.FindAllByResource(Resource)) vault.Remove(old);
    vault.Add(new PasswordCredential(Resource, displayId.ToString(), credential));
  }
  public static void Remove(Guid displayId)
  {
    var vault = new PasswordVault();
    try { var value = vault.Retrieve(Resource, displayId.ToString()); vault.Remove(value); } catch { }
  }
  public static string? Read(Guid? displayId)
  {
    if (displayId is null) return null;
    try { var value = new PasswordVault().Retrieve(Resource, displayId.Value.ToString()); value.RetrievePassword(); return value.Password; }
    catch { return null; }
  }
}
