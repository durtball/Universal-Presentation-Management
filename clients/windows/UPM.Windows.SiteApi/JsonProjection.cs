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

  public static long? NullableLong(this JsonElement value, string name)
  {
    if (value.ValueKind != JsonValueKind.Object ||
        !value.TryGetProperty(name, out var property) ||
        property.ValueKind != JsonValueKind.Number)
    {
      return null;
    }

    return property.TryGetInt64(out var result) ? result : null;
  }

  public static double? NullableNumber(this JsonElement value, string name)
  {
    if (value.ValueKind != JsonValueKind.Object ||
        !value.TryGetProperty(name, out var property) ||
        property.ValueKind != JsonValueKind.Number)
    {
      return null;
    }

    return property.TryGetDouble(out var result) ? result : null;
  }

  public static bool? NullableBool(this JsonElement value, string name)
  {
    if (value.ValueKind != JsonValueKind.Object || !value.TryGetProperty(name, out var property))
    {
      return null;
    }

    return property.ValueKind switch
    {
      JsonValueKind.True => true,
      JsonValueKind.False => false,
      _ => null,
    };
  }

  public static DateTimeOffset? NullableDate(this JsonElement value, string name) =>
      DateTimeOffset.TryParse(
          value.Text(name, string.Empty),
          CultureInfo.InvariantCulture,
          DateTimeStyles.RoundtripKind,
          out var result)
          ? result
          : null;

  public static Guid? Id(this JsonElement value, string name) =>
      Guid.TryParse(value.Text(name, string.Empty), out var id) ? id : null;

  public static long NumberOrDefault(this JsonElement value, string name, long fallback = 0) =>
      value.NullableLong(name) ?? fallback;

  public static long Number(this JsonElement value, string name) => value.NumberOrDefault(name);

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

  public static string Bytes(long? bytes) => bytes.HasValue ? Bytes(bytes.Value) : "—";

  public static string LocalTime(string? value) =>
      DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var time)
          ? time.ToLocalTime().ToString("g", CultureInfo.CurrentCulture)
          : "—";
}
