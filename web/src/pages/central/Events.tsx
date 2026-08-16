import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { centralApi } from "../../api/central";
import type { EventRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { Page } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";
import { DeletionDialog } from "../../components/DeletionDialog";
import { EventDialog } from "../../components/EventDialog";

const baseColumns: Column<EventRecord>[] = [
  {
    key: "name",
    label: "Event",
    value: (row) => row.name,
    render: (row) => (
      <Link to={`/admin/events/${row.event_id}`}>{row.name}</Link>
    ),
  },
  { key: "timezone", label: "Timezone", value: (row) => row.timezone },
  {
    key: "start",
    label: "Starts",
    value: (row) => row.starts_at,
    render: (row) => when(row.starts_at),
  },
  {
    key: "end",
    label: "Ends",
    value: (row) => row.ends_at,
    render: (row) => when(row.ends_at),
  },
  {
    key: "sites",
    label: "Deployed sites",
    value: (row) => row.deployments.length,
    numeric: true,
  },
];
export function Events() {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const result = useApi((signal) => api.events(signal), [api]);
  const [editing, setEditing] = useState<EventRecord | null>();
  const [deleting, setDeleting] = useState<EventRecord>();
  const columns = useMemo<Column<EventRecord>[]>(()=>[...baseColumns, {key:"actions",label:"Actions",value:()=>"",
    render:(row)=><div className="button-row"><Link className="button" to={`/admin/events/${row.event_id}`}>Open</Link><button className="button" type="button" onClick={()=>setEditing(row)}>Edit</button><Link className="button button--primary" to={`/admin/events/${row.event_id}#deploy`}>Deploy to Site</Link><button className="button button--danger" type="button" onClick={()=>setDeleting(row)}>Delete Event</button></div>}],[]);
  return (
    <Page
      eyebrow="Program"
      title="Events"
      description="Central-owned event programs and their Site deployments."
      actions={<button className="button button--primary" type="button" onClick={()=>setEditing(null)}>+ Create Event</button>}
    >
      <AdminBoundary>
        <PageState
          {...result}
          empty={(rows) => !rows.length}
          onRetry={result.refresh}
        >
          {(rows) => (
            <DataTable
              rows={rows}
              columns={columns}
              rowKey={(row) => row.event_id}
              label="Events"
            />
          )}
        </PageState>
        {deleting ? <DeletionDialog kind="Event" name={deleting.name}
          load={()=>api.eventDeletionImpact(deleting.event_id)}
          start={(confirmation)=>api.deleteEvent(deleting.event_id, confirmation)}
          close={()=>{setDeleting(undefined);result.refresh();}} /> : null}
        {editing !== undefined ? <EventDialog event={editing??undefined}
          save={values=>editing ? api.updateEvent(editing.event_id, values) : api.createEvent(values)}
          close={()=>{setEditing(undefined);result.refresh();}} /> : null}
      </AdminBoundary>
    </Page>
  );
}
