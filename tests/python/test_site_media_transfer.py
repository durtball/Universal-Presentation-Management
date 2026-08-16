from pathlib import Path
from uuid import UUID

from upm_site.config import SiteSettings
from upm_site.media.transfer import partial_path


def test_partial_transfer_path_is_opaque_and_stable(tmp_path: Path) -> None:
    settings = SiteSettings.model_construct(media_mount_path=str(tmp_path))
    transfer_id = UUID("018f0000-0000-7000-8000-000000000001")
    first = partial_path(settings, transfer_id)
    assert first == tmp_path / ".transfers" / f"{transfer_id}.partial"
    assert partial_path(settings, transfer_id) == first
    assert first.parent.is_dir()
