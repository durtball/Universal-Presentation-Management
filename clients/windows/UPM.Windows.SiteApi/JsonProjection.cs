using System.Globalization;
using System.Text.Json;

namespace UPM.Windows.SiteApi;

public static class JsonProjection
{
  public static string Text(this JsonElement value, string name, string fallback = "—") =>
      value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property) &&
      property.ValueKind is not (JsonValueKind.Null or JsonValueKind.Undefined)
          ? property.ValueKind == JsonValueKind.String ? property.GetString() ?? fallback : property.ToString()
          : fallback;

  public static Guid? Id(this JsonElement value, string name) =>
      Guid.TryParse(value.Text(name, string.Empty), out var id) ? id : null;

  public static long Number(this JsonElement value, string name) =>
      value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property) &&
      property.TryGetInt64(out var result) ? result : 0;

  public static JsonElement Child(this JsonElement value, string name) =>
      value.ValueKind == JsonValueKind.Object && value.TryGetProperty(name, out var property)
          ? property
          : default;

  public static IReadOnlyList<JsonElement> Items(this JsonElement value, string name = "items") =>
      value.Child(name) is { ValueKind: JsonValueKind.Array } array ? array.EnumerateArray().ToArray() : [];

  public static string Bytes(long bytes) => bytes <= 0
      ? "—"
      : bytes >= 1_073_741_824
          ? $"{bytes / 1_073_741_824d:0.0} GB"
          : $"{bytes / 1_048_576d:0.0} MB";

  public static string LocalTime(string? value) =>
      DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var time)
          ? time.ToLocalTime().ToString("g", CultureInfo.CurrentCulture)
          : "—";
}
