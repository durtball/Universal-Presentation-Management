using UPM.Windows.Shell;
using Xunit;

namespace UPM.Windows.Tests;

public sealed class AdaptiveLayoutTests
{
  [Theory]
  [InlineData(1000, 650, 2, 2, NavigationPresentation.Compact)]
  [InlineData(1280, 720, 4, 3, NavigationPresentation.Compact)]
  [InlineData(1920, 1080, 6, 4, NavigationPresentation.Expanded)]
  [InlineData(2560, 1440, 8, 5, NavigationPresentation.Expanded)]
  [InlineData(3440, 1440, 8, 5, NavigationPresentation.Expanded)]
  public void SupportedOperatorSizesProduceUsableLayouts(
      double width,
      double height,
      int metricColumns,
      int roomColumns,
      NavigationPresentation navigation)
  {
    var layout = AdaptiveLayoutPolicy.ForLogicalSize(width, height);

    Assert.Equal(metricColumns, layout.MetricColumns);
    Assert.Equal(roomColumns, layout.RoomColumns);
    Assert.Equal(navigation, layout.Navigation);
  }

  [Fact]
  public void MinimumLayoutHidesSecondaryTableColumns()
  {
    var layout = AdaptiveLayoutPolicy.ForLogicalSize(1000, 650);

    Assert.Equal(2, layout.HeaderRows);
    Assert.False(layout.ShowPresentationPresenter);
    Assert.False(layout.ShowPresentationModified);
    Assert.False(layout.ShowTransferSpeedAndElapsed);
  }

  [Fact]
  public void DpiScalingUsesLogicalPixelsRatherThanPhysicalPixels()
  {
    const double physicalWidth = 2000;
    const double scale = 2;

    var layout = AdaptiveLayoutPolicy.ForLogicalSize(physicalWidth / scale, 1300 / scale);

    Assert.Equal(NavigationPresentation.Compact, layout.Navigation);
    Assert.Equal(2, layout.MetricColumns);
  }

  [Fact]
  public void EveryNavigationAreaHasItsOwnPageType()
  {
    var areas = Navigation.Areas;

    Assert.Equal(9, areas.Count);
    Assert.Equal(9, areas.Select(area => area.PageType).Distinct().Count());
    Assert.Equal(
        ["Dashboard", "Intake", "Presentations", "Rooms", "Transfers", "Reviews", "Devices", "Activity", "Settings"],
        areas.Select(area => area.PageType));
  }
}
