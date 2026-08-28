using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;
using Microsoft.Extensions.DependencyInjection;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;
using Microsoft.UI;
using Microsoft.UI.Xaml.Media;

namespace UPM.SiteManager.Views;

public sealed partial class IntakePage : Page
{
  private readonly IOperatorContext context = App.Services.GetRequiredService<IOperatorContext>();
  private readonly LocalStateStore store = App.Services.GetRequiredService<LocalStateStore>();

  public IntakePage()
  {
    InitializeComponent();
    Loaded += OnLoaded;
    Unloaded += OnUnloaded;
  }

  private void OnDragOver(object sender, DragEventArgs args)
  {
    args.AcceptedOperation = DataPackageOperation.Copy;
    args.DragUIOverride.Caption = "Queue in UPM Site Manager";
    args.DragUIOverride.IsCaptionVisible = true;
  }

  private async void OnDrop(object sender, DragEventArgs args)
  {
    if (!args.DataView.Contains(StandardDataFormats.StorageItems))
    {
      return;
    }

    var paths = (await args.DataView.GetStorageItemsAsync()).Select(item => item.Path).ToArray();
    await QueuePathsAsync(paths);
  }

  private async void OnSelectFiles(object sender, RoutedEventArgs args)
  {
    var picker = new Windows.Storage.Pickers.FileOpenPicker();
    picker.FileTypeFilter.Add("*");
    WinRT.Interop.InitializeWithWindow.Initialize(
        picker,
        WinRT.Interop.WindowNative.GetWindowHandle(App.Services.GetRequiredService<MainWindow>()));
    var files = await picker.PickMultipleFilesAsync();
    await QueuePathsAsync(files.Select(file => file.Path));
  }

  private async void OnSelectFolder(object sender, RoutedEventArgs args)
  {
    var picker = new Windows.Storage.Pickers.FolderPicker();
    picker.FileTypeFilter.Add("*");
    WinRT.Interop.InitializeWithWindow.Initialize(
        picker,
        WinRT.Interop.WindowNative.GetWindowHandle(App.Services.GetRequiredService<MainWindow>()));
    var folder = await picker.PickSingleFolderAsync();
    if (folder is not null)
    {
      await QueuePathsAsync([folder.Path]);
    }
  }

  private async Task QueuePathsAsync(IEnumerable<string> paths)
  {
    if (context.Connection?.State != SiteConnectionState.Connected || context.Profile is null)
    {
      QueueStatus.Text = "Connect to a Site before accepting intake.";
      return;
    }

    await foreach (var item in IntakeEnumerator.EnumerateAsync(
                       paths,
                       context.Profile.ProfileId,
                       context.SelectedEventId))
    {
      await store.EnqueueAsync(item);
    }

    await ReloadAsync();
  }

  private void OnLoaded(object sender, RoutedEventArgs args)
  {
    context.Changed += OnContextChanged;
    _ = ReloadAsync();
  }

  private void OnUnloaded(object sender, RoutedEventArgs args) => context.Changed -= OnContextChanged;

  private void OnContextChanged(object? sender, OperatorContextChangedEventArgs args) =>
      DispatcherQueue.TryEnqueue(() => _ = ReloadAsync());

  private async Task ReloadAsync()
  {
    var transfers = await store.ListTransfersAsync();
    Queue.Items.Clear();
    foreach (var item in transfers.Where(item => item.SiteProfileId == context.Profile?.ProfileId))
    {
      var brush = item.State switch
      {
        TransferState.Hashing => new SolidColorBrush(Colors.Violet),
        TransferState.Complete => new SolidColorBrush(Colors.LightGreen),
        TransferState.RetryWaiting => new SolidColorBrush(Colors.Goldenrod),
        TransferState.Failed => new SolidColorBrush(Colors.OrangeRed),
        _ => new SolidColorBrush(Colors.Cyan),
      };
      Queue.Items.Add(new IntakeRow
      {
        Filename = item.RelativePath,
        Size = JsonProjection.Bytes(item.Length),
        Status = item.State.ToString().ToUpperInvariant(),
        Progress = item.Length > 0 ? 100d * item.BytesTransferred / item.Length : 0,
        StatusBrush = brush,
      });
    }

    EmptyState.Visibility = Queue.Items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    QueueStatus.Text = $"{transfers.Count(item => item.State == TransferState.Queued)} queued  •  {transfers.Count(item => item.State is TransferState.Hashing or TransferState.Uploading or TransferState.Verifying)} active  •  {transfers.Count(item => item.State == TransferState.Failed)} failed";
  }
}

public sealed class IntakeRow
{
  public string Filename { get; set; } = "—";
  public string Size { get; set; } = "—";
  public string Status { get; set; } = "UNKNOWN";
  public double Progress { get; set; }
  public Brush StatusBrush { get; set; } = new SolidColorBrush(Colors.Gray);
}
