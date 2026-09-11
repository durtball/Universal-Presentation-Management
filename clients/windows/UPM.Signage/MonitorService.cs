using System.Runtime.InteropServices;
using Windows.Graphics;

namespace UPM.Signage;
internal sealed record MonitorInfo(string Name, RectInt32 Bounds);
internal static partial class MonitorService
{
  private delegate bool MonitorCallback(IntPtr monitor, IntPtr hdc, ref NativeRect bounds, IntPtr data);
  [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
  [LibraryImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static partial bool EnumDisplayMonitors(IntPtr hdc, IntPtr clip, MonitorCallback callback, IntPtr data);
  public static IReadOnlyList<MonitorInfo> FindAll()
  {
    var result = new List<MonitorInfo>();
    EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (IntPtr _, IntPtr _, ref NativeRect value, IntPtr _) =>
    {
      result.Add(new($"Display {result.Count + 1}", new(value.Left, value.Top, value.Right - value.Left, value.Bottom - value.Top))); return true;
    }, IntPtr.Zero);
    if (result.Count == 0) result.Add(new("Primary display", new(0, 0, 1080, 1920)));
    return result;
  }
}
