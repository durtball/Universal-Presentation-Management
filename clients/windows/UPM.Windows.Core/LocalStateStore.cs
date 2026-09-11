using System.Globalization;
using System.Runtime.CompilerServices;
using Microsoft.Data.Sqlite;

namespace UPM.Windows.Core;

public sealed class LocalStateStore
{
  public LocalStateStore(string databasePath)
  {
    DatabasePath = databasePath;
  }

  public string DatabasePath { get; }

  private string ConnectionString => new SqliteConnectionStringBuilder
  {
    DataSource = DatabasePath,
    Mode = SqliteOpenMode.ReadWriteCreate,
    // The desktop store is long lived, but individual operations deliberately are not.
    // Disabling provider pooling guarantees completed operations release the database
    // file immediately (notably when tests or profile removal delete local state).
    Pooling = false,
  }.ToString();

  public async Task InitializeAsync(CancellationToken cancellationToken = default)
  {
    Directory.CreateDirectory(Path.GetDirectoryName(DatabasePath)!);
    await using var database = new SqliteConnection(ConnectionString);
    await database.OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS transfer_queue(
                transfer_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                event_id TEXT,
                source_path TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_volume TEXT,
                length INTEGER NOT NULL,
                source_modified TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                state INTEGER NOT NULL,
                bytes_transferred INTEGER NOT NULL,
                sha256 TEXT,
                retry_count INTEGER NOT NULL,
                retry_at TEXT,
                error TEXT,
                receipt_id TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_transfer_work ON transfer_queue(state,retry_at);
            CREATE TABLE IF NOT EXISTS site_profiles(
                profile_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                base_uri TEXT NOT NULL,
                remembered_username TEXT,
                canonical_site_id TEXT,
                canonical_site_display_name TEXT,
                certificate_thumbprint TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_connected_at TEXT,
                last_selected_event_id TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_site_profiles_base_uri ON site_profiles(base_uri);
            CREATE TABLE IF NOT EXISTS ui_preferences(
                preference_key TEXT PRIMARY KEY,
                preference_value TEXT NOT NULL
            );
            """;
    await command.ExecuteNonQueryAsync(cancellationToken);
    // Existing desktop databases predate terminal byte receipts.
    command.CommandText = "SELECT COUNT(*) FROM pragma_table_info('transfer_queue') WHERE name='receipt_id'";
    if (Convert.ToInt64(await command.ExecuteScalarAsync(cancellationToken), CultureInfo.InvariantCulture) == 0)
    {
      command.CommandText = "ALTER TABLE transfer_queue ADD COLUMN receipt_id TEXT";
      await command.ExecuteNonQueryAsync(cancellationToken);
    }
  }

  public async Task UpsertSiteProfileAsync(
      SiteProfile profile,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
            INSERT INTO site_profiles(
                profile_id, display_name, base_uri, remembered_username, canonical_site_id,
                canonical_site_display_name, certificate_thumbprint, created_at, updated_at,
                last_connected_at, last_selected_event_id)
            VALUES($id,$name,$uri,$username,$site_id,$site_name,$thumbprint,$created,$updated,$connected,$event_id)
            ON CONFLICT(profile_id) DO UPDATE SET
                display_name=excluded.display_name,
                base_uri=excluded.base_uri,
                remembered_username=excluded.remembered_username,
                canonical_site_id=excluded.canonical_site_id,
                canonical_site_display_name=excluded.canonical_site_display_name,
                certificate_thumbprint=excluded.certificate_thumbprint,
                updated_at=excluded.updated_at,
                last_connected_at=excluded.last_connected_at,
                last_selected_event_id=excluded.last_selected_event_id
            """;
    Add(command, "$id", profile.ProfileId);
    Add(command, "$name", profile.DisplayName);
    Add(command, "$uri", profile.BaseUri.AbsoluteUri);
    Add(command, "$username", profile.RememberedUsername);
    Add(command, "$site_id", profile.CanonicalSiteId);
    Add(command, "$site_name", profile.CanonicalSiteDisplayName);
    Add(command, "$thumbprint", profile.CertificateThumbprint);
    Add(command, "$created", Format(profile.CreatedAt));
    Add(command, "$updated", Format(profile.UpdatedAt));
    Add(command, "$connected", Format(profile.LastConnectedAt));
    Add(command, "$event_id", profile.LastSelectedEventId);
    try
    {
      await command.ExecuteNonQueryAsync(cancellationToken);
    }
    catch (SqliteException exception) when (exception.SqliteErrorCode == 19)
    {
      throw new InvalidOperationException(
          "A saved Site profile already uses this address.",
          exception);
    }
  }

  public async Task<IReadOnlyList<SiteProfile>> ListSiteProfilesAsync(
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = "SELECT * FROM site_profiles ORDER BY display_name COLLATE NOCASE";
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    var result = new List<SiteProfile>();
    while (await reader.ReadAsync(cancellationToken))
    {
      result.Add(ReadSiteProfile(reader));
    }

    return result;
  }

  public async Task<SiteProfile?> GetSiteProfileAsync(
      Guid profileId,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = "SELECT * FROM site_profiles WHERE profile_id=$id";
    Add(command, "$id", profileId);
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    return await reader.ReadAsync(cancellationToken) ? ReadSiteProfile(reader) : null;
  }

  public async Task DeleteSiteProfileAsync(
      Guid profileId,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    await using var transaction = await database.BeginTransactionAsync(cancellationToken);
    var transfers = database.CreateCommand();
    transfers.Transaction = (SqliteTransaction)transaction;
    transfers.CommandText = "SELECT COUNT(*) FROM transfer_queue WHERE profile_id=$id AND state NOT IN ($complete,$cancelled)";
    Add(transfers, "$id", profileId);
    Add(transfers, "$complete", (int)TransferState.Complete);
    Add(transfers, "$cancelled", (int)TransferState.Cancelled);
    if (Convert.ToInt64(await transfers.ExecuteScalarAsync(cancellationToken), CultureInfo.InvariantCulture) > 0)
    {
      throw new InvalidOperationException("Cannot remove a Site profile with unfinished transfers.");
    }

    var command = database.CreateCommand();
    command.Transaction = (SqliteTransaction)transaction;
    command.CommandText = "DELETE FROM site_profiles WHERE profile_id=$id";
    Add(command, "$id", profileId);
    await command.ExecuteNonQueryAsync(cancellationToken);
    await transaction.CommitAsync(cancellationToken);
  }

  public async Task SetPreferenceAsync(
      string key,
      string value,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
            INSERT INTO ui_preferences(preference_key,preference_value) VALUES($key,$value)
            ON CONFLICT(preference_key) DO UPDATE SET preference_value=excluded.preference_value
            """;
    Add(command, "$key", key);
    Add(command, "$value", value);
    await command.ExecuteNonQueryAsync(cancellationToken);
  }

  public async Task<string?> GetPreferenceAsync(
      string key,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = "SELECT preference_value FROM ui_preferences WHERE preference_key=$key";
    Add(command, "$key", key);
    return (string?)await command.ExecuteScalarAsync(cancellationToken);
  }

  public async Task EnqueueAsync(TransferItem item, CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
            INSERT INTO transfer_queue(
                transfer_id,profile_id,event_id,source_path,original_filename,relative_path,
                source_volume,length,source_modified,idempotency_key,state,bytes_transferred,
                sha256,retry_count,retry_at,error,receipt_id) VALUES(
                $id,$profile,$event,$path,$name,$relative,$volume,$length,$modified,$key,
                $state,$bytes,$hash,$retry,$retry_at,$error,$receipt)
            ON CONFLICT(idempotency_key) DO NOTHING
            """;
    Add(command, "$id", item.TransferId);
    Add(command, "$profile", item.SiteProfileId);
    Add(command, "$event", item.EventId);
    Add(command, "$path", item.SourcePath);
    Add(command, "$name", item.OriginalFilename);
    Add(command, "$relative", item.RelativePath);
    Add(command, "$volume", item.SourceVolume);
    Add(command, "$length", item.Length);
    Add(command, "$modified", Format(item.SourceModifiedAt));
    Add(command, "$key", item.IdempotencyKey);
    Add(command, "$state", (int)item.State);
    Add(command, "$bytes", item.BytesTransferred);
    Add(command, "$hash", item.Sha256);
    Add(command, "$retry", item.RetryCount);
    Add(command, "$retry_at", Format(item.RetryAt));
    Add(command, "$error", item.Error);
    Add(command, "$receipt", item.ReceiptId);
    await command.ExecuteNonQueryAsync(cancellationToken);
  }

  public async Task UpdateAsync(
      Guid transferId,
      TransferState state,
      long bytes = 0,
      string? hash = null,
      int retry = 0,
      DateTimeOffset? retryAt = null,
      string? error = null,
      Guid? receiptId = null,
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
            UPDATE transfer_queue SET state=$state,bytes_transferred=$bytes,
            sha256=COALESCE($hash,sha256),retry_count=$retry,retry_at=$retry_at,error=$error,
            receipt_id=COALESCE($receipt,receipt_id)
            WHERE transfer_id=$id
            """;
    Add(command, "$state", (int)state);
    Add(command, "$bytes", bytes);
    Add(command, "$hash", hash);
    Add(command, "$retry", retry);
    Add(command, "$retry_at", Format(retryAt));
    Add(command, "$error", error);
    Add(command, "$receipt", receiptId);
    Add(command, "$id", transferId);
    await command.ExecuteNonQueryAsync(cancellationToken);
  }

  public async Task<IReadOnlyList<TransferItem>> ListTransfersAsync(
      CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    await using var command = database.CreateCommand();
    command.CommandText = "SELECT * FROM transfer_queue ORDER BY rowid DESC";
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    var result = new List<TransferItem>();
    while (await reader.ReadAsync(cancellationToken))
    {
      result.Add(ReadTransfer(reader));
    }

    return result;
  }

  public async IAsyncEnumerable<TransferItem> LoadPendingAsync(
      [EnumeratorCancellation] CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = "SELECT * FROM transfer_queue WHERE state NOT IN ($received,$complete,$failed,$cancelled) AND (retry_at IS NULL OR retry_at <= $now) ORDER BY rowid";
    Add(command, "$received", (int)TransferState.ReceivedBySite);
    Add(command, "$complete", (int)TransferState.Complete);
    Add(command, "$failed", (int)TransferState.Failed);
    Add(command, "$cancelled", (int)TransferState.Cancelled);
    Add(command, "$now", Format(DateTimeOffset.UtcNow));
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    while (await reader.ReadAsync(cancellationToken))
    {
      yield return ReadTransfer(reader);
    }
  }

  public async Task<TransferItem?> ClaimDueAsync(CancellationToken cancellationToken = default)
  {
    await using var database = await OpenAsync(cancellationToken);
    var command = database.CreateCommand();
    command.CommandText = """
        UPDATE transfer_queue SET state=$hashing,retry_at=NULL
        WHERE transfer_id=(
          SELECT transfer_id FROM transfer_queue
          WHERE state NOT IN ($received,$complete,$failed,$cancelled)
            AND (retry_at IS NULL OR retry_at <= $now)
          ORDER BY rowid LIMIT 1
        )
        RETURNING *
        """;
    Add(command, "$hashing", (int)TransferState.Hashing);
    Add(command, "$received", (int)TransferState.ReceivedBySite);
    Add(command, "$complete", (int)TransferState.Complete);
    Add(command, "$failed", (int)TransferState.Failed);
    Add(command, "$cancelled", (int)TransferState.Cancelled);
    Add(command, "$now", Format(DateTimeOffset.UtcNow));
    await using var reader = await command.ExecuteReaderAsync(cancellationToken);
    return await reader.ReadAsync(cancellationToken) ? ReadTransfer(reader) : null;
  }

  private async Task<SqliteConnection> OpenAsync(CancellationToken cancellationToken)
  {
    var database = new SqliteConnection(ConnectionString);
    await database.OpenAsync(cancellationToken);
    return database;
  }

  private static SiteProfile ReadSiteProfile(SqliteDataReader reader) => new(
      Guid.Parse(reader.GetString(0)),
      reader.GetString(1),
      new Uri(reader.GetString(2), UriKind.Absolute),
      ReadString(reader, 3),
      ReadGuid(reader, 4),
      ReadString(reader, 5),
      ReadString(reader, 6),
      ParseDate(reader.GetString(7)),
      ParseDate(reader.GetString(8)),
      ReadDate(reader, 9),
      ReadGuid(reader, 10));

  private static TransferItem ReadTransfer(SqliteDataReader reader) => new(
      Guid.Parse(reader.GetString(0)),
      Guid.Parse(reader.GetString(1)),
      ReadGuid(reader, 2),
      reader.GetString(3),
      reader.GetString(4),
      reader.GetString(5),
      ReadString(reader, 6),
      reader.GetInt64(7),
      ParseDate(reader.GetString(8)),
      reader.GetString(9),
      (TransferState)reader.GetInt32(10),
      reader.GetInt64(11),
      ReadString(reader, 12),
      reader.GetInt32(13),
      ReadDate(reader, 14),
      ReadString(reader, 15),
      ReadGuid(reader, 16));

  private static string? ReadString(SqliteDataReader reader, int ordinal) =>
      reader.IsDBNull(ordinal) ? null : reader.GetString(ordinal);

  private static Guid? ReadGuid(SqliteDataReader reader, int ordinal) =>
      reader.IsDBNull(ordinal) ? null : Guid.Parse(reader.GetString(ordinal));

  private static DateTimeOffset? ReadDate(SqliteDataReader reader, int ordinal) =>
      reader.IsDBNull(ordinal) ? null : ParseDate(reader.GetString(ordinal));

  private static DateTimeOffset ParseDate(string value) =>
      DateTimeOffset.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);

  private static string Format(DateTimeOffset value) => value.ToString("O", CultureInfo.InvariantCulture);

  private static object? Format(DateTimeOffset? value) => value.HasValue ? Format(value.Value) : null;

  private static void Add(SqliteCommand command, string name, object? value) =>
      command.Parameters.AddWithValue(name, value?.ToString() ?? (object)DBNull.Value);
}
