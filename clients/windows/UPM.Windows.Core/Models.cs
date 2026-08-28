using System.Security.Cryptography;
using System.Text;

namespace UPM.Windows.Core;

public enum TransferState
{
  Queued,
  Hashing,
  Uploading,
  Verifying,
  ReceivedBySite,
  Matching,
  NeedsReview,
  Assigned,
  Complete,
  RetryWaiting,
  Failed,
  Cancelled,
}

public sealed record SiteProfile(
    Guid ProfileId,
    string DisplayName,
    Uri BaseUri,
    string? RememberedUsername,
    Guid? CanonicalSiteId,
    string? CanonicalSiteDisplayName,
    string? CertificateThumbprint,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt,
    DateTimeOffset? LastConnectedAt,
    Guid? LastSelectedEventId)
{
  public string Status => CanonicalSiteId.HasValue ? "IDENTITY VERIFIED" : "NOT VERIFIED";
}

public sealed record TransferItem(
    Guid TransferId,
    Guid SiteProfileId,
    Guid? EventId,
    string SourcePath,
    string OriginalFilename,
    string RelativePath,
    string? SourceVolume,
    long Length,
    DateTimeOffset SourceModifiedAt,
    string IdempotencyKey,
    TransferState State = TransferState.Queued,
    long BytesTransferred = 0,
    string? Sha256 = null,
    int RetryCount = 0,
    DateTimeOffset? RetryAt = null,
    string? Error = null);

public static class TransferIdentity
{
  public static string Create(
      Guid profileId,
      Guid? eventId,
      string path,
      long length,
      DateTimeOffset modified)
  {
    var input = $"{profileId:N}|{eventId:N}|{Path.GetFullPath(path).ToUpperInvariant()}|{length}|{modified.UtcTicks}";
    return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(input))).ToLowerInvariant();
  }
}

public static class SiteAddressNormalizer
{
  public const int DefaultAppliancePort = 9080;

  public static Uri Normalize(string address, int? explicitPort = null)
  {
    var input = address.Trim();
    if (string.IsNullOrWhiteSpace(input))
    {
      throw new FormatException("Enter a Site address or host name.");
    }

    if (!input.Contains("://", StringComparison.Ordinal))
    {
      input = $"http://{input}";
    }

    if (!Uri.TryCreate(input, UriKind.Absolute, out var parsed) ||
        parsed.Scheme is not ("http" or "https") ||
        string.IsNullOrWhiteSpace(parsed.Host) ||
        !string.IsNullOrEmpty(parsed.UserInfo) ||
        parsed.Query.Length > 0 ||
        parsed.Fragment.Length > 0)
    {
      throw new FormatException("Enter a valid HTTP or HTTPS Site address.");
    }

    var builder = new UriBuilder(parsed)
    {
      Path = "/",
      Query = string.Empty,
      Fragment = string.Empty,
    };
    if (explicitPort is <= 0 or > 65535)
    {
      throw new FormatException("Port must be between 1 and 65535.");
    }

    if (explicitPort.HasValue)
    {
      builder.Port = explicitPort.Value;
    }
    else if (parsed.IsDefaultPort && parsed.Scheme == Uri.UriSchemeHttp)
    {
      builder.Port = DefaultAppliancePort;
    }

    return builder.Uri;
  }
}

public interface ICredentialVault
{
  ValueTask SaveAsync(Guid profileId, string sessionCookie, CancellationToken cancellationToken);
  ValueTask<string?> ReadAsync(Guid profileId, CancellationToken cancellationToken);
  ValueTask ForgetAsync(Guid profileId, CancellationToken cancellationToken);
}
