"""Read-only Site-local event program views for autonomous operation."""
# ruff: noqa: E501

from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from upm_site.persistence.models import (
    Event,
    EventParticipation,
    PersonProjection,
    Presentation,
    PresentationPresenter,
    PresentationSession,
    Room,
    RoomAssignment,
    SessionParticipant,
)
from upm_site.persistence.models import Session as ProgramSession


def register_program_routes(app: FastAPI, db: Callable[[], Iterator[Session]]) -> None:
    DbSession = Annotated[Session, Depends(db)]

    @app.get("/api/v1/events/{event_id}/program", tags=["program"])
    def event_program(event_id: UUID, session: DbSession) -> dict[str, object]:
        event = session.get(Event, event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        participants = session.scalars(
            select(EventParticipation)
            .where(EventParticipation.event_id == event_id, EventParticipation.active.is_(True))
            .order_by(EventParticipation.created_at)
        ).all()
        sessions = session.scalars(
            select(ProgramSession)
            .where(ProgramSession.event_id == event_id, ProgramSession.active.is_(True))
            .order_by(ProgramSession.starts_at, ProgramSession.sort_order)
        ).all()
        presentations = session.scalars(
            select(Presentation)
            .where(Presentation.event_id == event_id, Presentation.active.is_(True))
            .order_by(Presentation.title)
        ).all()
        people = {
            item.person_id: item
            for item in session.scalars(
                select(PersonProjection).where(
                    PersonProjection.person_id.in_([item.person_id for item in participants])
                )
            )
        }
        session_presenters: dict[UUID, list[SessionParticipant]] = {}
        for link in session.scalars(
            select(SessionParticipant).where(
                SessionParticipant.session_id.in_([item.session_id for item in sessions]),
                SessionParticipant.active.is_(True),
            )
        ):
            session_presenters.setdefault(link.session_id, []).append(link)
        presentation_sessions: dict[UUID, list[PresentationSession]] = {}
        for link in session.scalars(
            select(PresentationSession).where(
                PresentationSession.presentation_id.in_(
                    [item.presentation_id for item in presentations]
                ),
                PresentationSession.active.is_(True),
            )
        ):
            presentation_sessions.setdefault(link.presentation_id, []).append(link)
        presentation_presenters: dict[UUID, list[PresentationPresenter]] = {}
        for link in session.scalars(
            select(PresentationPresenter).where(
                PresentationPresenter.presentation_id.in_(
                    [item.presentation_id for item in presentations]
                ),
                PresentationPresenter.active.is_(True),
            )
        ):
            presentation_presenters.setdefault(link.presentation_id, []).append(link)
        room_assignments = {
            assignment.session_id: (assignment, room)
            for assignment, room in session.execute(
                select(RoomAssignment, Room)
                .join(Room, Room.room_id == RoomAssignment.room_id)
                .where(
                    RoomAssignment.session_id.in_([item.session_id for item in sessions]),
                    RoomAssignment.active.is_(True),
                )
            )
        }
        return {
            "event": {
                "event_id": event.event_id,
                "name": event.name,
                "description": event.description,
                "timezone": event.timezone,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
            },
            "participants": [
                {
                    "event_participation_id": item.event_participation_id,
                    "person_id": item.person_id,
                    "person_display_name": people[item.person_id].display_name,
                    "display_name": item.display_name,
                    "professional_title": item.professional_title,
                    "organization": item.organization,
                    "participant_status": item.participant_status,
                    "is_presenter": item.is_presenter,
                }
                for item in participants
            ],
            "sessions": [
                {
                    "session_id": item.session_id,
                    "title": item.title,
                    "subtitle": item.subtitle,
                    "session_code": item.session_code,
                    "starts_at": item.starts_at,
                    "ends_at": item.ends_at,
                    "location_name": item.location_name,
                    "room_mapping_status": (
                        "mapped"
                        if item.session_id in room_assignments
                        else "unmapped"
                        if item.location_name
                        else "unassigned"
                    ),
                    "assigned_room": (
                        {
                            "room_id": room_assignments[item.session_id][1].room_id,
                            "label": room_assignments[item.session_id][1].label,
                        }
                        if item.session_id in room_assignments
                        else None
                    ),
                    "status": item.status,
                    "presenters": [
                        {
                            "session_participant_id": link.session_participant_id,
                            "event_participation_id": link.event_participation_id,
                            "role": link.role,
                            "presenter_order": link.presenter_order,
                            "primary_presenter": link.primary_presenter,
                        }
                        for link in session_presenters.get(item.session_id, [])
                    ],
                }
                for item in sessions
            ],
            "presentations": [
                {
                    "presentation_id": item.presentation_id,
                    "title": item.title,
                    "presentation_code": item.presentation_code,
                    "workflow_status": item.workflow_status,
                    "processing_status": item.processing_status,
                    "sessions": [
                        {
                            "presentation_session_id": link.presentation_session_id,
                            "session_id": link.session_id,
                            "primary_session": link.primary_session,
                        }
                        for link in presentation_sessions.get(item.presentation_id, [])
                    ],
                    "presenters": [
                        {
                            "presentation_presenter_id": link.presentation_presenter_id,
                            "event_participation_id": link.event_participation_id,
                            "role": link.role,
                            "presenter_order": link.presenter_order,
                            "primary_presenter": link.primary_presenter,
                        }
                        for link in presentation_presenters.get(item.presentation_id, [])
                    ],
                }
                for item in presentations
            ],
        }

    @app.get("/admin/program", response_class=HTMLResponse, tags=["administration"])
    def program_page() -> str:
        return """<!doctype html><html><head><title>UPM Site Program</title><style>
body{font:14px system-ui;background:#091019;color:#dbeaf3;margin:24px}button,input{margin:4px;
padding:7px;background:#111d29;color:#dbeaf3;border:1px solid #2f657c}pre{background:#0d1721;
padding:12px;overflow:auto}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
</style></head><body>
<nav><a href="/admin/central-registration">Central registration</a> |
<a href="/admin/event-deployments">Deployments</a> | <a href="/admin/program">Program</a></nav>
<h1>Site-local event program</h1><p>Site commits locally; Central recovery sync is asynchronous.</p>
<div class="row"><input id="eventName" placeholder="New Event name"><input id="timezone"
value="UTC" aria-label="IANA timezone"><button onclick="createEvent()">CREATE EVENT</button>
<button onclick="load()">REFRESH</button></div><div id="events"></div><hr>
<div class="row"><input id="programFile" type="file" accept=".csv,.xlsx"><button
onclick="stage()">STAGE PROGRAM</button><button id="commit" disabled onclick="commitImport()">
COMMIT IMPORT</button></div><pre id="status">Select an Event.</pre><div id="out"></div><script>
let eventId=null,batchId=null,csrf=null;async function token(){if(csrf)return csrf;const r=await
fetch('/api/v1/auth/session');if(r.ok)csrf=(await r.json()).csrf_token;return csrf||''}async function
write(url,opt={}){opt.headers={...(opt.headers||{}),'X-CSRF-Token':await token()};const r=await
fetch(url,opt);const data=await r.json();if(!r.ok)throw Error(data.detail||r.statusText);return data}
async function load(){const ds=await(await fetch('/api/v1/event-deployments')).json();events.
replaceChildren();for(const d of ds){const id=d.event_id||d.central_event_id,b=document.createElement
('button');b.textContent=`${d.event_name||id} — ${d.status}`;b.onclick=()=>openEvent(id);events.append
(b)}}async function openEvent(id){eventId=id;const p=await(await fetch(`/api/v1/events/${id}/program`))
.json();out.innerHTML=`<pre>${JSON.stringify(p,null,2)}</pre>`;status.textContent=`Selected ${id}`}
async function createEvent(){try{const e=await write('/api/v1/events',{method:'POST',headers:
{'Content-Type':'application/json'},body:JSON.stringify({name:eventName.value,timezone:timezone.value})});
await load();await openEvent(e.event_id)}catch(e){status.textContent=e.message}}async function stage(){if
(!eventId)return status.textContent='Select an Event first.';const file=programFile.files[0];if(!file)
return status.textContent='Select a CSV or XLSX file.';const body=new FormData();body.append('file',file);try
{const data=await write(`/api/v1/events/${eventId}/program-imports`,{method:'POST',body});batchId=
data.import_batch_id;status.textContent=JSON.stringify(data,null,2);commit.disabled=data.error_count!==0}
catch(e){status.textContent=e.message}}async function commitImport(){try{const data=await write(
`/api/v1/program-imports/${batchId}/commit`,{method:'POST'});status.textContent=JSON.stringify(data,
null,2);commit.disabled=true;await openEvent(eventId)}catch(e){status.textContent=e.message}}load()
</script></body></html>"""
