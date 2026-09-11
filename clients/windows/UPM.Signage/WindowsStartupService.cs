using Microsoft.Win32;
namespace UPM.Signage;
internal static class WindowsStartupService
{
  public static void SetEnabled(bool enabled)
  {
    using var key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run");
    if (enabled) key.SetValue("UPM Signage", $"\"{Environment.ProcessPath}\" --player");
    else key.DeleteValue("UPM Signage", false);
  }
}
