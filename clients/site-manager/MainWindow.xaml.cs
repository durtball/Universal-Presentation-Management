using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using UPM.SiteManager.Views;
using Windows.Graphics;

namespace UPM.SiteManager;

public sealed partial class MainWindow : Window
{
  private static readonly IReadOnlyDictionary<string, Type> Pages =
      new Dictionary<string, Type>(StringComparer.Ordinal)
      {
        ["dashboard"] = typeof(DashboardPage),
        ["intake"] = typeof(IntakePage),
        ["presentations"] = typeof(PresentationsPage),
        ["rooms"] = typeof(RoomsPage),
        ["transfers"] = typeof(TransfersPage),
        ["reviews"] = typeof(ReviewSessionsPage),
        ["devices"] = typeof(DevicesPage),
        ["activity"] = typeof(ActivityPage),
        ["settings"] = typeof(SettingsPage),
      };

  public MainWindow()
  {
    InitializeComponent();
    SystemBackdrop = new Microsoft.UI.Xaml.Media.MicaBackdrop();
    AppWindow.Resize(new SizeInt32(1280, 800));
    ContentFrame.Navigate(typeof(DashboardPage));
    PrimaryNavigation.SelectedItem = PrimaryNavigation.MenuItems[0];
  }

  private void Navigate(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
  {
    if (args.SelectedItem is not NavigationViewItem item ||
        item.Tag is not string tag ||
        !Pages.TryGetValue(tag, out var pageType))
    {
      return;
    }

    if (ContentFrame.CurrentSourcePageType != pageType)
    {
      ContentFrame.Navigate(pageType);
    }
  }
}
