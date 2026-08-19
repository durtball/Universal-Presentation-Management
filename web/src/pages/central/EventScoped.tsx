import { useCallback, useMemo, useState, type ReactNode } from "react";
import { centralApi } from "../../api/central";
import type { EventDeployment, EventRecord, Row } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EventPicker } from "../../components/EventPicker";
import { EventDeploymentDialog } from "../../components/EventDeploymentDialog";
import { Empty, ErrorSurface, Loading } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate } from "../../components/presentationMedia";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const string = (row: Row, key: string) => row[key] as string | undefined;
const presenterName = (row: Row) =>
  row.display_name ||
  row.person_display_name ||
  [row.given_name, row.family_name].filter(Boolean).join(" ");
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
        key: "presenters",
        label: "Presenters",
        value: (row) => Array.isArray(row.presenters)
          ? row.presenters.map((item) => String((item as Row).display_name || "")).filter(Boolean).join(", ")
          : "—",
      },
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
      { key: "name", label: "Presenter", value: presenterName },
      { key: "title", label: "Title", value: (row) => row.professional_title },
      {
        key: "organization",
        label: "Organization",
        value: (row) => row.organization,
      },
      {
        key: "email",
        label: "Email",
        value: (row) => row.primary_email,
      },
      {
        key: "sessions",
        label: "Sessions",
        value: (row) => Array.isArray(row.sessions)
          ? row.sessions.map((item) => String((item as Row).title || "")).filter(Boolean).join(", ")
          : "—",
      },
      {
        key: "person",
        label: "Permanent person",
        value: (row) => row.person_id,
        render: (row) => <code>{string(row, "person_id")?.slice(0, 8)}…</code>,
      },
      {
        key: "external",
        label: "External ID",
        value: (row) => Array.isArray(row.external_identifiers)
          ? row.external_identifiers.map((item) => {
              const value = item as Row;
              return `${value.namespace}:${value.external_id}`;
            }).join(", ")
          : "—",
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
        value: (row) => Array.isArray(row.sessions) ? row.sessions.map((value) => `${String((value as Row).title || (value as Row).session_id)} · ${formatDate((value as Row).starts_at as string)} · ${String((value as Row).room || "—")}`).join("; ") : "—",
      },
      { key: "file", label: "Current file", value: (row) => { const media = Array.isArray(row.media_versions) ? row.media_versions[0] as Row : undefined; return media ? `${String(media.original_filename)} · ${formatBytes(media.size_bytes as number)} · ${formatDate(media.received_at as string)}` : "—"; } },
      { key: "version", label: "Version", value: (row) => { const media = Array.isArray(row.media_versions) ? row.media_versions[0] as Row : undefined; return media ? `v${String(media.version_number)}` : "—"; } },
      {
        key: "presenters",
        label: "Presenters",
        value: (row) => Array.isArray(row.presenters)
          ? row.presenters.map((item) => String((item as Row).display_name || "")).filter(Boolean).join(", ")
          : "—",
      },
    ],
  },
};

export function EventScoped({ type }: { type: keyof typeof definitions }) {
  const definition = definitions[type];
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const events = useApi((signal) => api.events(signal), [api]);
  const [eventId, setEventId] = useState("");
  const [selectedPresentation, setSelectedPresentation] = useState<Row>();
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
            actions={type === "presentations" ? (row) => <><button className="button button--small" onClick={() => setSelectedPresentation(row)}>View Details</button>{Array.isArray(row.media_versions) && (row.media_versions[0] as Row | undefined)?.download_url ? <a className="button button--small" href={String((row.media_versions[0] as Row).download_url)}>Download current</a> : null}<button className="button button--small" disabled title="No Central workstation/Agent context is available">Open unavailable</button></> : undefined}
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
      {selectedPresentation && <PresentationDetails row={selectedPresentation} onClose={() => setSelectedPresentation(undefined)} />}
    </Page>
  );
}

function PresentationDetails({ row, onClose }: { row: Row; onClose: () => void }) {
  const sessions = Array.isArray(row.sessions) ? row.sessions as Row[] : [];
  const versions = Array.isArray(row.media_versions) ? row.media_versions as Row[] : [];
  return <div className="dialog-backdrop"><section className="dialog media-detail" role="dialog" aria-modal="true"><header><div><span className="eyebrow">{String(row.presentation_identifier || "Presentation")}</span><h2>{String(row.title)}</h2></div><button className="button" onClick={onClose}>Close</button></header><dl className="detail-grid"><div><dt>Presenter(s)</dt><dd>{Array.isArray(row.presenters) ? row.presenters.map((value) => String((value as Row).display_name)).join(", ") : "—"}</dd></div><div><dt>Readiness</dt><dd>{String(row.workflow_status)}</dd></div></dl><h3>Associated sessions</h3>{sessions.map((value) => <p key={String(value.session_id)}><strong>{String(value.title || value.session_id)}</strong><br/><small>{String(value.session_code || "No external ID")} · {formatDate(value.starts_at as string)} · {String(value.room || "No room")}</small></p>)}<h3>Version history</h3>{versions.length ? versions.map((value, index) => <article key={String(value.presentation_version_id)}><strong>v{String(value.version_number)}{index === 0 ? " — Current" : ""}</strong><dl className="detail-grid"><div><dt>Original filename</dt><dd>{String(value.original_filename)}</dd></div><div><dt>Type / size</dt><dd>{String(value.mime_type || "Unknown")} · {formatBytes(value.size_bytes as number)}</dd></div><div><dt>Received / source</dt><dd>{formatDate(value.received_at as string)} · {String(value.source || "—")}</dd></div><div><dt>Confirmed</dt><dd>{formatDate(value.confirmed_at as string)} · {String(value.confirmed_by || "—")}</dd></div><div><dt>SHA-256</dt><dd><code>{String(value.sha256 || "—")}</code></dd></div></dl>{index === 0 && value.download_url ? <a className="button button--small" href={String(value.download_url)}>Download current</a> : null}</article>) : <p>No confirmed versions.</p>}</section></div>;
}

export function EventDetail({ event, onChanged }: { event: EventRecord; onChanged?:()=>void }) {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const sites = useApi((signal) => api.sites(signal), [api]);
  const [deploying,setDeploying]=useState(false); const [error,setError]=useState<unknown>();
  const loadPreview=useCallback((siteId:string)=>api.deploymentPreview(event.event_id,siteId),[api,event.event_id]);
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
      <Panel title="Deploy program" description="Review and publish a complete ADR-0007 snapshot. Missing Site rooms are created automatically and stable room UUIDs are reused on updates.">
        {sites.data?.some(site=>site.enrollment_state==="active")?<button className="button button--primary" type="button" onClick={()=>setDeploying(true)}>Deploy to Site</button>:<p>No active enrolled Site is available.</p>}
        {error?<ErrorSurface error={error}/>:null}
        <DataTable rows={event.deployments as EventDeployment[]} columns={[
          { key: "site", label: "Site", value: (row) => row.site_name||row.site_id },
          { key: "status", label: "Status", value: (row) => row.status,
            render: (row) => <StatusBadge value={row.synchronization_state || row.status} /> },
          { key: "revision", label: "Revision", value: (row) => `${row.applied_revision || 0} / ${row.desired_revision || 0}` },
          { key: "failure", label: "Failure", value: (row) => row.failure_reason || "—" },
          {key:"action",label:"Action",value:()=>"",render:(row)=><button className="button" type="button" onClick={async()=>{setError(undefined);try{if(row.status==="failed")await api.retryDeployment(row.deployment_id);else await api.pushDeployment(row.deployment_id);onChanged?.();}catch(reason){setError(reason);}}}>{row.status==="failed"?"Retry":row.update_available?"Deploy Update":"Redeploy"}</button>},
        ]} rowKey={(row) => String(row.deployment_id)} label="Site deployments" />
      </Panel>
      {deploying&&sites.data?<EventDeploymentDialog eventName={event.name} sites={sites.data} loadPreview={loadPreview} deploy={siteId=>api.deployEvent(event.event_id,siteId)} push={deploymentId=>api.pushDeployment(deploymentId)} close={()=>setDeploying(false)} completed={()=>{setDeploying(false);onChanged?.();}}/>:null}
    </Page>
  );
}
