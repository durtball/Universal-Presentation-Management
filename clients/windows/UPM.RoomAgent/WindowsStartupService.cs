using Microsoft.Win32;

namespace UPM.RoomAgent;

internal static class WindowsStartupService
{
  private const string KeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
  private const string ValueName = "UPM Room Agent";
  public static void SetEnabled(bool enabled)
  {
    using var key = Registry.CurrentUser.CreateSubKey(KeyPath);
    if (enabled) key.SetValue(ValueName, $"\"{Environment.ProcessPath}\" --startup");
    else key.DeleteValue(ValueName, false);
  }
}
