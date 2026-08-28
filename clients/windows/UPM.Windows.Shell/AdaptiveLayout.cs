namespace UPM.Windows.Shell;

public enum NavigationPresentation
{
  Compact,
  Expanded,
}

public sealed record AdaptiveLayout(
    NavigationPresentation Navigation,
    int HeaderRows,
    int MetricColumns,
    int RoomColumns,
    bool ShowPresentationPresenter,
    bool ShowPresentationModified,
    bool ShowTransferSpeedAndElapsed);

public static class AdaptiveLayoutPolicy
{
  public const double MinimumPracticalWidth = 1000;
  public const double MinimumPracticalHeight = 650;

  public static AdaptiveLayout ForLogicalSize(double width, double height)
  {
    if (width < MinimumPracticalWidth || height < MinimumPracticalHeight)
    {
      throw new ArgumentOutOfRangeException(
          nameof(width),
          $"Site Manager requires at least {MinimumPracticalWidth}x{MinimumPracticalHeight} logical pixels.");
    }

    return new AdaptiveLayout(
        width >= 1320 ? NavigationPresentation.Expanded : NavigationPresentation.Compact,
        width >= 1180 ? 1 : 2,
        width switch
        {
          >= 2400 => 8,
          >= 1800 => 6,
          >= 1280 => 4,
          _ => 2,
        },
        width switch
        {
          >= 2200 => 5,
          >= 1700 => 4,
          >= 1280 => 3,
          _ => 2,
        },
        width >= 1500,
        width >= 1180,
        width >= 1300);
  }
}
