"""Deterministic cleanup for abandoned same-target ingestion artifacts."""

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from upm_shared.enums import MediaAvailability
from upm_shared.jobs import utc_now
from upm_site.config import SiteSettings
from upm_site.media.storage import remove_stale_staging_files
from upm_site.persistence.database import create_site_engine, create_site_session_factory
from upm_site.persistence.models import MediaObject, StorageTarget

logger = logging.getLogger(__name__)


def cleanup_stale_ingestions(
    session_factory: sessionmaker[Session], *, older_than: timedelta
) -> list[str]:
    """Fail abandoned staging records and remove only inactive old artifacts."""
    if older_than.total_seconds() <= 0:
        raise ValueError("cleanup age must be positive")
    cutoff = utc_now() - older_than
    targets: list[StorageTarget]
    active_by_target: dict[UUID, set[UUID]] = {}
    with session_factory.begin() as session:
        targets = list(
            session.scalars(select(StorageTarget).where(StorageTarget.enabled.is_(True)))
        )
        stale = session.scalars(
            select(MediaObject)
            .where(
                MediaObject.availability == MediaAvailability.STAGING,
                MediaObject.updated_at < cutoff,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for media in stale:
            media.availability = MediaAvailability.FAILED
            media.failure_reason = "abandoned staging upload expired"
        active = session.execute(
            select(MediaObject.storage_target_id, MediaObject.media_object_id).where(
                or_(
                    MediaObject.availability == MediaAvailability.FINALIZING,
                    and_(
                        MediaObject.availability == MediaAvailability.STAGING,
                        MediaObject.updated_at >= cutoff,
                    ),
                )
            )
        )
        for target_id, media_id in active:
            active_by_target.setdefault(target_id, set()).add(media_id)
        for target in targets:
            session.expunge(target)

    removed: list[str] = []
    for target in targets:
        paths = remove_stale_staging_files(
            target,
            active_media_ids=active_by_target.get(target.storage_target_id, set()),
            older_than=older_than,
        )
        for path in paths:
            removed.append(str(path))
            logger.info(
                "staging_cleanup",
                extra={
                    "storage_target_id": str(target.storage_target_id),
                    "staging_path_name": path.name,
                },
            )
    return removed


def main() -> None:
    settings = SiteSettings()
    factory = create_site_session_factory(create_site_engine(settings))
    removed = cleanup_stale_ingestions(
        factory, older_than=timedelta(seconds=settings.staging_max_age_seconds)
    )
    logger.info("staging_cleanup_completed", extra={"removed_count": len(removed)})


if __name__ == "__main__":
    main()
