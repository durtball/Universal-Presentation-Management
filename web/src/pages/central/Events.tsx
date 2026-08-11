import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { centralApi } from "../../api/central";
import type { EventRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { ErrorSurface } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const columns: Column<EventRecord>[] = [
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
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [error, setError] = useState<unknown>();
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setError(undefined);
    try { await api.createEvent(name, timezone); setName(""); result.refresh(); }
    catch (caught) { setError(caught); }
  };
  return (
    <Page
      eyebrow="Program"
      title="Events"
      description="Central-owned event programs and their Site deployments."
    >
      <AdminBoundary>
        <Panel title="Create event">
          <form className="inline-form" onSubmit={submit}>
            <label className="field">Event name<input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></label>
            <label className="field">Timezone<input className="input" required value={timezone} onChange={(e) => setTimezone(e.target.value)} /></label>
            <button className="button button--primary">Create event</button>
          </form>
          {error != null ? <ErrorSurface error={error} /> : null}
        </Panel>
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
      </AdminBoundary>
    </Page>
  );
}
