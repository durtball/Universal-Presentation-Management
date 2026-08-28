using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.ApplicationModel.DataTransfer;

namespace UPM.SiteManager.Views;

public sealed partial class IntakePage : Page
{
  public IntakePage() => InitializeComponent();

  private void DragOver(object sender, DragEventArgs args)
  {
    args.AcceptedOperation = DataPackageOperation.Copy;
    args.DragUIOverride.Caption = "Queue in UPM Site Manager";
    args.DragUIOverride.IsCaptionVisible = true;
  }

  private async void Drop(object sender, DragEventArgs args)
  {
    if (!args.DataView.Contains(StandardDataFormats.StorageItems))
    {
      return;
    }

    foreach (var item in await args.DataView.GetStorageItemsAsync())
    {
      Queue.Items.Add(item.Path);
    }
  }
}
