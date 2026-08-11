import { useMemo, useState } from "react";
import { centralApi } from "../../api/central";
import type { RoomMapping } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { Empty, ErrorSurface, Loading } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary } from "./Shared";

const columns: Column<RoomMapping>[] = [
  { key: "imported", label: "Imported room", value: (row) => row.imported_label },
  { key: "status", label: "Mapping", value: (row) => row.mapping_status,
    render: (row) => <StatusBadge value={row.mapping_status} /> },
  { key: "physical", label: "Site room", value: (row) => row.target_room_label || "—" },
  { key: "id", label: "Site room UUID", value: (row) => row.target_room_id || "—" },
];

export function RoomMappings() {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const events = useApi((signal) => api.events(signal), [api]);
  const sites = useApi((signal) => api.sites(signal), [api]);
  const [eventId, setEventId] = useState("");
  const [siteId, setSiteId] = useState("");
  const selectedEvent = eventId || events.data?.[0]?.event_id || "";
  const selectedSite = siteId || sites.data?.[0]?.site_id || "";
  const mappings = useApi(
    (signal) => selectedEvent && selectedSite
      ? api.roomMappings(selectedEvent, selectedSite, signal) : Promise.resolve([]),
    [api, selectedEvent, selectedSite],
  );
  const [editing, setEditing] = useState<RoomMapping>();
  const [roomId, setRoomId] = useState("");
  const [roomLabel, setRoomLabel] = useState("");
  const [error, setError] = useState<unknown>();
  return (
    <Page eyebrow="Site resources" title="Room mapping"
      description="Map imported logical room labels to physical rooms independently for each Site.">
      <AdminBoundary>
        {events.loading || sites.loading ? <Loading /> : events.error || sites.error ? (
          <ErrorSurface error={events.error || sites.error} />
        ) : !events.data?.length || !sites.data?.length ? <Empty title="Event and Site required" /> : <>
          <div className="inline-form">
            <label className="field">Event<select className="input" value={selectedEvent} onChange={(e) => setEventId(e.target.value)}>
              {events.data.map((event) => <option key={event.event_id} value={event.event_id}>{event.name}</option>)}
            </select></label>
            <label className="field">Site<select className="input" value={selectedSite} onChange={(e) => setSiteId(e.target.value)}>
              {sites.data.map((site) => <option key={site.site_id} value={site.site_id}>{site.display_name}</option>)}
            </select></label>
          </div>
          {mappings.loading ? <Loading /> : mappings.error ? <ErrorSurface error={mappings.error} /> : (
            <DataTable rows={mappings.data ?? []} columns={columns} rowKey={(row) => row.normalized_imported_label}
              label="Imported room mappings" actions={(row) => <button className="button button--small" onClick={() => {
                setEditing(row); setRoomId(row.target_room_id || ""); setRoomLabel(row.target_room_label || "");
              }}>Reconcile</button>} />
          )}
          {editing ? <Panel title={`Map ${editing.imported_label}`} description="Use a room UUID from the Site-local Rooms API. Existing deliberate Site assignments are never overwritten.">
            <div className="inline-form">
              <label className="field">Site room UUID<input className="input" value={roomId} onChange={(e) => setRoomId(e.target.value)} /></label>
              <label className="field">Site room label<input className="input" value={roomLabel} onChange={(e) => setRoomLabel(e.target.value)} /></label>
              <button className="button button--primary" onClick={async () => {
                setError(undefined);
                try { await api.saveRoomMapping({ site_id: selectedSite, imported_label: editing.imported_label,
                  target_room_id: roomId, target_room_label: roomLabel, mapping_status: "mapped" });
                  setEditing(undefined); mappings.refresh(); }
                catch (caught) { setError(caught); }
              }}>Save mapping</button>
              <button className="button" onClick={async () => {
                await api.saveRoomMapping({ site_id: selectedSite, imported_label: editing.imported_label,
                  target_room_id: null, target_room_label: null, mapping_status: "unmapped" });
                setEditing(undefined); mappings.refresh();
              }}>Leave unassigned</button>
            </div>
            {error != null ? <ErrorSurface error={error} /> : null}
          </Panel> : null}
        </>}
      </AdminBoundary>
    </Page>
  );
}
