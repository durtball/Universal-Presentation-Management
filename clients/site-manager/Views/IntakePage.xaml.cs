using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;
using Microsoft.Extensions.DependencyInjection;
using UPM.Windows.Core;
using UPM.Windows.SiteApi;
using UPM.Windows.Transfers;

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

    if (context.Connection?.State != SiteConnectionState.Connected || context.Profile is null)
    {
      QueueStatus.Text = "Connect to a Site before accepting intake.";
      return;
    }

    var paths = (await args.DataView.GetStorageItemsAsync()).Select(item => item.Path).ToArray();
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
      Queue.Items.Add($"{item.State.ToString().ToUpperInvariant(),-16}  {item.RelativePath}");
    }

    EmptyState.Visibility = Queue.Items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    QueueStatus.Text = $"{transfers.Count(item => item.State == TransferState.Queued)} queued  •  {transfers.Count(item => item.State is TransferState.Hashing or TransferState.Uploading or TransferState.Verifying)} active  •  {transfers.Count(item => item.State == TransferState.Failed)} failed";
  }
}
