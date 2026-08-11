"""Exercise browser-facing auth, XLSX import, room mapping, and ADR-0007 deployment."""

import argparse
import io
import json
import time
from datetime import UTC, datetime

import httpx
from openpyxl import Workbook


def response(response: httpx.Response) -> dict | list:
    response.raise_for_status()
    return response.json()


def workbook(suffix: str) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Program"
    sheet.append(
        [
            "Speaker Name",
            "Speaker Email",
            "Company",
            "Session Title",
            "Session Code",
            "Presentation Title",
            "Presentation Code",
            "Start",
            "End",
            "Room",
        ]
    )
    sheet.append(
        [
            "Alex Example",
            f"alex.{suffix}@example.test",
            "UPM Validation",
            "Functional Import Session",
            f"SESSION-{suffix}",
            "Functional Import Presentation",
            f"PRESENTATION-{suffix}",
            "2027-09-10T14:00:00-05:00",
            "2027-09-10T15:00:00-05:00",
            f"Imported Ballroom {suffix}",
        ]
    )
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--central", default="http://localhost:28080")
    parser.add_argument("--site", default="http://localhost:29080")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    args = parser.parse_args()
    suffix = datetime.now(UTC).strftime("%H%M%S")

    with httpx.Client(timeout=30) as central, httpx.Client(timeout=30) as site:
        assert central.get(f"{args.central}/api/v1/admin/events").status_code == 401
        login = response(
            central.post(
                f"{args.central}/api/v1/auth/login",
                json={"username": args.username, "password": args.password},
            )
        )
        central.headers["X-CSRF-Token"] = login["csrf_token"]
        event = response(
            central.post(
                f"{args.central}/api/v1/admin/events",
                json={"name": f"Admin Import Smoke {suffix}", "timezone": "America/Chicago"},
            )
        )
        event_id = event["event_id"]
        staged = response(
            central.post(
                f"{args.central}/api/v1/admin/events/{event_id}/imports",
                files={
                    "file": (
                        "realistic-program.xlsx",
                        workbook(suffix),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                data={"importer_type": "program"},
            )
        )
        batch_id = staged["import_batch_id"]
        detail = response(central.get(f"{args.central}/api/v1/admin/imports/{batch_id}"))
        for row in detail["rows"]:
            if row["conflict_state"]:
                response(
                    central.post(
                        f"{args.central}/api/v1/admin/import-rows/{row['import_row_id']}/decision",
                        json={
                            "action": "create_person",
                            "reason": "Smoke-test presenter has a distinct strong email",
                        },
                    )
                )
        if detail["conflict_count"]:
            detail = response(central.get(f"{args.central}/api/v1/admin/imports/{batch_id}"))
        assert detail["status"] == "ready", detail
        assert detail["preview_counts"]["people_or_presenters"] == 1
        assert detail["preview_counts"]["sessions"] == 1
        assert detail["preview_counts"]["presentations"] == 1
        assert detail["preview_counts"]["unresolved_room_mappings"] == 1
        committed = response(central.post(f"{args.central}/api/v1/admin/imports/{batch_id}/commit"))
        assert committed["status"] == "committed"
        participants = response(
            central.get(f"{args.central}/api/v1/admin/events/{event_id}/participants")
        )
        sessions = response(central.get(f"{args.central}/api/v1/admin/events/{event_id}/sessions"))
        presentations = response(
            central.get(f"{args.central}/api/v1/admin/events/{event_id}/presentations")
        )
        assert len(participants) == len(sessions) == len(presentations) == 1
        assert sessions[0]["presenters"][0]["display_name"] == "Alex Example"
        assert presentations[0]["presenters"][0]["display_name"] == "Alex Example"
        assert presentations[0]["sessions"][0]["session_id"] == sessions[0]["session_id"]

        registration = response(site.get(f"{args.site}/api/v1/central-registration"))
        room = response(
            site.post(f"{args.site}/api/v1/rooms", json={"label": f"Physical Salon {suffix}"})
        )
        response(
            central.put(
                f"{args.central}/api/v1/admin/room-mappings",
                json={
                    "site_id": registration["site_id"],
                    "imported_label": sessions[0]["location_name"],
                    "target_room_id": room["room_id"],
                    "target_room_label": room["label"],
                    "mapping_status": "mapped",
                },
            )
        )
        deployment = response(
            central.post(
                f"{args.central}/api/v1/admin/events/{event_id}/deployments",
                json={"site_id": registration["site_id"]},
            )
        )
        program = None
        for _ in range(30):
            candidate = site.get(f"{args.site}/api/v1/events/{event_id}/program")
            if candidate.status_code == 200:
                program = candidate.json()
                break
            time.sleep(1)
        assert program is not None, "Site did not apply the deployment within 30 seconds"
        assert program["sessions"][0]["room_mapping_status"] == "mapped"
        assert program["sessions"][0]["assigned_room"]["room_id"] == room["room_id"]
        logout = central.post(f"{args.central}/api/v1/auth/logout")
        assert logout.status_code == 204
        assert central.get(f"{args.central}/api/v1/admin/events").status_code == 401
        print(
            json.dumps(
                {
                    "event_id": event_id,
                    "import_batch_id": batch_id,
                    "deployment_id": deployment["deployment_id"],
                    "room_id": room["room_id"],
                    "presenters": len(participants),
                    "sessions": len(sessions),
                    "presentations": len(presentations),
                    "site_room_status": program["sessions"][0]["room_mapping_status"],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
