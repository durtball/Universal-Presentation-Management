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
  { key: "physical", label: "Site room", value: (row) => row.target_room_label || "Not assigned" },
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
  return (
    <Page eyebrow="Site resources" title="Room mapping"
      description="Review how imported room labels resolve to physical rooms at each Site.">
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
          <Panel title="Server-authoritative mappings" description="Assign or reconcile physical rooms from the selected Site's Rooms page; internal identifiers are never entered manually here.">
            {mappings.loading ? <Loading /> : mappings.error ? <ErrorSurface error={mappings.error} /> :
              <DataTable rows={mappings.data ?? []} columns={columns} rowKey={(row) => row.normalized_imported_label} label="Imported room mappings" />}
          </Panel>
        </>}
      </AdminBoundary>
    </Page>
  );
}
