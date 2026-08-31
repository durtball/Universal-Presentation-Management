using System.Globalization;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace UPM.Windows.Agent;

public sealed class AgentStateStore(string databasePath)
{
  public const int SchemaVersion = 2;
  private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
  private string ConnectionString => new SqliteConnectionStringBuilder { DataSource = databasePath, Pooling = false }.ToString();

  public async Task InitializeAsync(CancellationToken cancellationToken = default)
  {
    Directory.CreateDirectory(Path.GetDirectoryName(databasePath)!);
    await using var db = await OpenAsync(cancellationToken);
    await using var command = db.CreateCommand();
    command.CommandText = """
      PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
      INSERT INTO schema_info(version) SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_info);
      CREATE TABLE IF NOT EXISTS singleton_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY, session_identifier TEXT, title TEXT NOT NULL, presenter TEXT,
        room_id TEXT NOT NULL, room_name TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
        cancelled INTEGER NOT NULL, revision INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS assets(
        asset_id TEXT PRIMARY KEY, kind INTEGER NOT NULL, version_id TEXT NOT NULL,
        presentation_id TEXT, session_id TEXT, room_id TEXT, event_day TEXT, rotation_scope INTEGER,
        original_filename TEXT NOT NULL, effective_filename TEXT NOT NULL, sha256 TEXT NOT NULL,
        expected_size INTEGER NOT NULL, managed_path TEXT NOT NULL, verified INTEGER NOT NULL,
        authoritative INTEGER NOT NULL, created_at TEXT NOT NULL);
      CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_asset_version ON assets(version_id, kind);
      CREATE TABLE IF NOT EXISTS pending_changes(
        change_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, presentation_id TEXT, local_version_id TEXT NOT NULL,
        base_version_id TEXT, idempotency_key TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL,
        managed_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, state INTEGER NOT NULL,
        created_at TEXT NOT NULL, error TEXT);
      CREATE TABLE IF NOT EXISTS library_paths(
        asset_id TEXT NOT NULL, session_id TEXT NOT NULL, visible_path TEXT NOT NULL,
        PRIMARY KEY(asset_id, session_id));
      UPDATE schema_info SET version=2 WHERE version<2;
      """;
    await command.ExecuteNonQueryAsync(cancellationToken);
  }

  public Task SaveProvisioningAsync(ProvisioningState value, CancellationToken ct = default) => SetAsync("provisioning", value, ct);
  public Task<ProvisioningState?> GetProvisioningAsync(CancellationToken ct = default) => GetAsync<ProvisioningState>("provisioning", ct);
  public Task SaveRevisionsAsync(SyncRevisions value, CancellationToken ct = default) => SetAsync("revisions", value, ct);
  public async Task<SyncRevisions> GetRevisionsAsync(CancellationToken ct = default) =>
      await GetAsync<SyncRevisions>("revisions", ct) ?? new(0, 0, 0, 0);
  public Task SetLastSuccessfulSyncAsync(DateTimeOffset value, CancellationToken ct = default) => SetAsync("last_sync", value, ct);
  public Task<DateTimeOffset?> GetLastSuccessfulSyncAsync(CancellationToken ct = default) => GetAsync<DateTimeOffset?>("last_sync", ct);
  public Task SaveSettingsAsync(AgentSettings value, CancellationToken ct = default) => SetAsync("settings", value, ct);
  public Task<AgentSettings?> GetSettingsAsync(CancellationToken ct = default) => GetAsync<AgentSettings>("settings", ct);
  public Task SaveBrandingAsync(BrandingState value, CancellationToken ct = default) => SetAsync("branding", value, ct);
  public Task<BrandingState?> GetBrandingAsync(CancellationToken ct = default) => GetAsync<BrandingState>("branding", ct);
  public Task SetSiteConnectedAsync(bool value, CancellationToken ct = default) => SetAsync("site_connected", value, ct);
  public async Task<bool> GetSiteConnectedAsync(CancellationToken ct = default) => await GetAsync<bool?>("site_connected", ct) ?? false;

  public async Task ClearProvisioningAsync(CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var cmd = db.CreateCommand();
    cmd.CommandText = "DELETE FROM singleton_state WHERE key IN ('provisioning','site_connected','last_sync','revisions')";
    await cmd.ExecuteNonQueryAsync(ct);
  }

  public async Task UpsertSessionAsync(AgentSession session, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = """
      INSERT INTO sessions VALUES($id,$identifier,$title,$presenter,$room,$room_name,$start,$end,$cancelled,$revision)
      ON CONFLICT(session_id) DO UPDATE SET session_identifier=excluded.session_identifier,title=excluded.title,
      presenter=excluded.presenter,room_id=excluded.room_id,room_name=excluded.room_name,starts_at=excluded.starts_at,
      ends_at=excluded.ends_at,cancelled=excluded.cancelled,revision=excluded.revision
      WHERE excluded.revision >= sessions.revision
      """;
    Add(command, "$id", session.SessionId); Add(command, "$identifier", session.SessionIdentifier);
    Add(command, "$title", session.Title); Add(command, "$presenter", session.Presenter); Add(command, "$room", session.RoomId);
    Add(command, "$room_name", session.RoomName); Add(command, "$start", Format(session.StartsAt)); Add(command, "$end", Format(session.EndsAt));
    Add(command, "$cancelled", session.Cancelled ? 1 : 0); Add(command, "$revision", session.Revision);
    await command.ExecuteNonQueryAsync(ct);
  }

  public async Task<IReadOnlyList<AgentSession>> ListSessionsAsync(CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "SELECT * FROM sessions ORDER BY starts_at,session_id";
    await using var reader = await command.ExecuteReaderAsync(ct); var result = new List<AgentSession>();
    while (await reader.ReadAsync(ct)) result.Add(new(Guid.Parse(reader.GetString(0)), StringOrNull(reader, 1), reader.GetString(2), StringOrNull(reader, 3), Guid.Parse(reader.GetString(4)), reader.GetString(5), Parse(reader.GetString(6)), Parse(reader.GetString(7)), reader.GetInt32(8) != 0, reader.GetInt64(9)));
    return result;
  }

  public async Task UpsertAssetAsync(AgentAsset asset, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = """
      INSERT INTO assets VALUES($id,$kind,$version,$presentation,$session,$room,$day,$scope,$original,$effective,$hash,$size,$path,$verified,$authoritative,$created)
      ON CONFLICT(asset_id) DO UPDATE SET version_id=excluded.version_id,original_filename=excluded.original_filename,
      effective_filename=excluded.effective_filename,sha256=excluded.sha256,expected_size=excluded.expected_size,
      managed_path=excluded.managed_path,verified=excluded.verified,authoritative=excluded.authoritative
      """;
    object?[] values = [asset.AssetId, (int)asset.Kind, asset.VersionId, asset.PresentationId, asset.SessionId, asset.RoomId, asset.EventDay?.ToString("O", CultureInfo.InvariantCulture), asset.RotationScope is null ? null : (int)asset.RotationScope, asset.OriginalFilename, asset.EffectiveFilename, asset.Sha256, asset.ExpectedSize, asset.ManagedPath, asset.Verified ? 1 : 0, asset.Authoritative ? 1 : 0, Format(asset.CreatedAt)];
    string[] names = ["$id", "$kind", "$version", "$presentation", "$session", "$room", "$day", "$scope", "$original", "$effective", "$hash", "$size", "$path", "$verified", "$authoritative", "$created"];
    for (var i = 0; i < names.Length; i++) Add(command, names[i], values[i]); await command.ExecuteNonQueryAsync(ct);
  }

  public async Task<AgentAsset?> GetVerifiedVersionAsync(Guid versionId, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "SELECT * FROM assets WHERE version_id=$id AND verified=1 ORDER BY authoritative DESC,created_at DESC LIMIT 1"; Add(command, "$id", versionId);
    await using var reader = await command.ExecuteReaderAsync(ct); return await reader.ReadAsync(ct) ? ReadAsset(reader) : null;
  }

  public async Task<IReadOnlyList<AgentAsset>> ListAssetsAsync(CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "SELECT * FROM assets ORDER BY created_at DESC";
    await using var reader = await command.ExecuteReaderAsync(ct); var result = new List<AgentAsset>();
    while (await reader.ReadAsync(ct)) result.Add(ReadAsset(reader));
    return result;
  }

  public async Task<string?> GetLibraryPathAsync(Guid assetId, Guid? sessionId, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "SELECT visible_path FROM library_paths WHERE asset_id=$asset AND session_id=$session";
    Add(command, "$asset", assetId); Add(command, "$session", sessionId?.ToString() ?? string.Empty);
    return (string?)await command.ExecuteScalarAsync(ct);
  }

  public async Task SetLibraryPathAsync(Guid assetId, Guid? sessionId, string path, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "INSERT INTO library_paths VALUES($asset,$session,$path) ON CONFLICT(asset_id,session_id) DO UPDATE SET visible_path=excluded.visible_path";
    Add(command, "$asset", assetId); Add(command, "$session", sessionId?.ToString() ?? string.Empty); Add(command, "$path", path);
    await command.ExecuteNonQueryAsync(ct);
  }

  public async Task EnqueueChangeAsync(PendingLocalChange change, CancellationToken ct = default)
  {
    await using var db = await OpenAsync(ct); await using var command = db.CreateCommand();
    command.CommandText = "INSERT INTO pending_changes VALUES($id,$session,$presentation,$local,$base,$key,$name,$path,$hash,$size,$state,$created,$error) ON CONFLICT(idempotency_key) DO NOTHING";
    object?[] values = [change.ChangeId, change.SessionId, change.PresentationId, change.LocalVersionId, change.BaseVersionId, change.IdempotencyKey, change.OriginalFilename, change.ManagedPath, change.Sha256, change.Size, (int)change.State, Format(change.CreatedAt), change.Error];
    string[] names = ["$id", "$session", "$presentation", "$local", "$base", "$key", "$name", "$path", "$hash", "$size", "$state", "$created", "$error"];
    for (var i = 0; i < names.Length; i++) Add(command, names[i], values[i]); await command.ExecuteNonQueryAsync(ct);
  }

  public async Task<int> GetSchemaVersionAsync(CancellationToken ct = default)
  { await using var db = await OpenAsync(ct); await using var cmd = db.CreateCommand(); cmd.CommandText = "SELECT version FROM schema_info"; return Convert.ToInt32(await cmd.ExecuteScalarAsync(ct), CultureInfo.InvariantCulture); }
  private async Task SetAsync<T>(string key, T value, CancellationToken ct) { await using var db = await OpenAsync(ct); await using var cmd = db.CreateCommand(); cmd.CommandText = "INSERT INTO singleton_state VALUES($key,$value) ON CONFLICT(key) DO UPDATE SET value=excluded.value"; Add(cmd, "$key", key); Add(cmd, "$value", JsonSerializer.Serialize(value, JsonOptions)); await cmd.ExecuteNonQueryAsync(ct); }
  private async Task<T?> GetAsync<T>(string key, CancellationToken ct) { await using var db = await OpenAsync(ct); await using var cmd = db.CreateCommand(); cmd.CommandText = "SELECT value FROM singleton_state WHERE key=$key"; Add(cmd, "$key", key); var value = (string?)await cmd.ExecuteScalarAsync(ct); return value is null ? default : JsonSerializer.Deserialize<T>(value, JsonOptions); }
  private async Task<SqliteConnection> OpenAsync(CancellationToken ct) { var db = new SqliteConnection(ConnectionString); await db.OpenAsync(ct); return db; }
  private static AgentAsset ReadAsset(SqliteDataReader r) => new(Guid.Parse(r.GetString(0)), (AssetKind)r.GetInt32(1), Guid.Parse(r.GetString(2)), GuidOrNull(r, 3), GuidOrNull(r, 4), GuidOrNull(r, 5), r.IsDBNull(6) ? null : DateOnly.Parse(r.GetString(6), CultureInfo.InvariantCulture), (RotationScope?)IntOrNull(r, 7), r.GetString(8), r.GetString(9), r.GetString(10), r.GetInt64(11), r.GetString(12), r.GetInt32(13) != 0, r.GetInt32(14) != 0, Parse(r.GetString(15)));
  private static Guid? GuidOrNull(SqliteDataReader r, int i) => r.IsDBNull(i) ? null : Guid.Parse(r.GetString(i));
  private static int? IntOrNull(SqliteDataReader r, int i) => r.IsDBNull(i) ? null : r.GetInt32(i);
  private static string? StringOrNull(SqliteDataReader r, int i) => r.IsDBNull(i) ? null : r.GetString(i);
  private static DateTimeOffset Parse(string value) => DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
  private static string Format(DateTimeOffset value) => value.ToString("O", CultureInfo.InvariantCulture);
  private static void Add(SqliteCommand c, string n, object? v) => c.Parameters.AddWithValue(n, v?.ToString() ?? (object)DBNull.Value);
}
