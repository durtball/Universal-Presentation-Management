import { useMemo, useState, type ReactNode } from "react";
import { centralApi } from "../../api/central";
import type { EventRecord, Row } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EventPicker } from "../../components/EventPicker";
import { Empty, ErrorSurface, Loading } from "../../components/Feedback";
import { Page } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const string = (row: Row, key: string) => row[key] as string | undefined;
const definitions: Record<
  string,
  {
    title: string;
    description: string;
    load: "sessions" | "participants" | "presentations";
    columns: Column<Row>[];
    filter?: (row: Row) => boolean;
  }
> = {
  sessions: {
    title: "Sessions",
    description:
      "Scheduled program sessions, timing, rooms, status, and presenter relationships.",
    load: "sessions",
    columns: [
      { key: "title", label: "Session", value: (row) => row.title },
      { key: "code", label: "Code", value: (row) => row.session_code },
      {
        key: "start",
        label: "Starts",
        value: (row) => row.starts_at,
        render: (row) => when(row.starts_at),
      },
      { key: "room", label: "Location", value: (row) => row.location_name },
      {
        key: "status",
        label: "Status",
        value: (row) => row.status,
        render: (row) => <StatusBadge value={row.status} />,
      },
    ],
  },
  presenters: {
    title: "Presenters",
    description:
      "Event participation records linked back to permanent Central people.",
    load: "participants",
    filter: (row) => Boolean(row.is_presenter),
    columns: [
      { key: "name", label: "Presenter", value: (row) => row.display_name },
      { key: "title", label: "Title", value: (row) => row.professional_title },
      {
        key: "organization",
        label: "Organization",
        value: (row) => row.organization,
      },
      {
        key: "person",
        label: "Permanent person",
        value: (row) => row.person_id,
        render: (row) => <code>{string(row, "person_id")?.slice(0, 8)}…</code>,
      },
      {
        key: "status",
        label: "Status",
        value: (row) => row.participant_status,
        render: (row) => <StatusBadge value={row.participant_status} />,
      },
    ],
  },
  presentations: {
    title: "Presentations",
    description:
      "Logical presentations and their session and presenter associations.",
    load: "presentations",
    columns: [
      { key: "title", label: "Presentation", value: (row) => row.title },
      { key: "code", label: "Code", value: (row) => row.presentation_code },
      {
        key: "workflow",
        label: "Workflow",
        value: (row) => row.workflow_status,
        render: (row) => <StatusBadge value={row.workflow_status} />,
      },
      {
        key: "processing",
        label: "Processing",
        value: (row) => row.processing_status,
        render: (row) => <StatusBadge value={row.processing_status} />,
      },
      {
        key: "sessions",
        label: "Sessions",
        value: (row) => (Array.isArray(row.sessions) ? row.sessions.length : 0),
        numeric: true,
      },
      {
        key: "presenters",
        label: "Presenters",
        value: (row) =>
          Array.isArray(row.presenters) ? row.presenters.length : 0,
        numeric: true,
      },
    ],
  },
};

export function EventScoped({ type }: { type: keyof typeof definitions }) {
  const definition = definitions[type];
  const { adminToken } = useSession();
  const api = useMemo(() => centralApi(adminToken), [adminToken]);
  const events = useApi((signal) => api.events(signal), [api]);
  const [eventId, setEventId] = useState("");
  const selected = eventId || events.data?.[0]?.event_id || "";
  const records = useApi(
    (signal) =>
      selected ? api[definition.load](selected, signal) : Promise.resolve([]),
    [api, selected, definition.load],
  );
  let content: ReactNode;
  if (events.loading) content = <Loading />;
  else if (events.error)
    content = <ErrorSurface error={events.error} onRetry={events.refresh} />;
  else if (!events.data?.length)
    content = (
      <Empty title="No events available">
        Create an event through the existing API before managing its program.
      </Empty>
    );
  else
    content = (
      <>
        <EventPicker
          events={events.data}
          value={selected}
          onChange={setEventId}
        />
        {records.loading ? (
          <Loading />
        ) : records.error ? (
          <ErrorSurface error={records.error} onRetry={records.refresh} />
        ) : (
          <DataTable
            rows={(records.data ?? []).filter(
              definition.filter ?? (() => true),
            )}
            columns={definition.columns}
            rowKey={(row) =>
              String(
                row.session_id ??
                  row.event_participation_id ??
                  row.presentation_id,
              )
            }
            label={definition.title}
          />
        )}
      </>
    );
  return (
    <Page
      eyebrow="Program"
      title={definition.title}
      description={definition.description}
    >
      <AdminBoundary>{content}</AdminBoundary>
    </Page>
  );
}

export function EventDetail({ event }: { event: EventRecord }) {
  return (
    <Page
      eyebrow="Event detail"
      title={event.name}
      description={event.description || "No description provided."}
    >
      <div className="metrics">
        <div className="metric">
          <span>Timezone</span>
          <strong>{event.timezone}</strong>
        </div>
        <div className="metric">
          <span>Starts</span>
          <strong>{when(event.starts_at)}</strong>
        </div>
        <div className="metric">
          <span>Ends</span>
          <strong>{when(event.ends_at)}</strong>
        </div>
        <div className="metric">
          <span>Site deployments</span>
          <strong>{event.deployments.length}</strong>
        </div>
      </div>
    </Page>
  );
}
