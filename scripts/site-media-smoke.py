"""Seed an isolated Site and verify a real upload through the running Site API."""

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from upm_shared.enums import StorageHealth, StorageType
from upm_shared.identifiers import new_uuid7
from upm_site.persistence.models import Site, StorageTarget


def request_json(request: Request) -> dict[str, object] | list[object]:
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed local smoke URL
        return json.loads(response.read())


database_url = os.environ["UPM_SITE_DATABASE_URL"]
media_root = Path(os.environ.get("UPM_SITE_MEDIA_MOUNT_PATH", "/var/lib/upm/media"))
site_id = new_uuid7()
target_id = new_uuid7()
with Session(create_engine(database_url)) as session, session.begin():
    session.add(Site(site_id=site_id, display_name="Media smoke Site"))
    session.flush()
    session.add(
        StorageTarget(
            storage_target_id=target_id,
            site_id=site_id,
            display_name="Media smoke primary",
            storage_type=StorageType.LOCAL_FILESYSTEM,
            root_path=str(media_root),
            enabled=True,
            primary_media=True,
            health=StorageHealth.UNKNOWN,
            warning_free_bytes=0,
            critical_free_bytes=0,
            safety_reserve_bytes=0,
        )
    )

content = b"%PDF-1.7\nUPM Site media smoke\n"
uploaded = request_json(
    Request(
        "http://127.0.0.1:8080/api/v1/media/ingestions"
        f"?site_id={site_id}&category=open_file&expected_size={len(content)}",
        data=content,
        headers={
            "Content-Type": "application/pdf",
            "X-UPM-Original-Filename": "smoke.pdf",
            "Idempotency-Key": f"smoke-{site_id}",
        },
        method="POST",
    )
)
assert isinstance(uploaded, dict)
assert uploaded["availability"] == "available"
media_id = uploaded["media_object_id"]
metadata = request_json(Request(f"http://127.0.0.1:8080/api/v1/media/{media_id}"))
status = request_json(Request(f"http://127.0.0.1:8080/api/v1/media/{media_id}/status"))
health = request_json(Request("http://127.0.0.1:8080/api/v1/storage-targets/health"))
assert isinstance(metadata, dict) and metadata["original_filename"] == "smoke.pdf"
assert isinstance(status, dict) and status["availability"] == "available"
assert isinstance(health, list) and health and health[0]["available"] is True
print(json.dumps({"media_object_id": media_id, "bytes": len(content), "health": "healthy"}))
