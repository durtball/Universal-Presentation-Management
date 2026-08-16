import { useMemo, useState } from "react";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import type { CentralMediaImport, PresentationMediaRow, Row } from "../api/types";
import { DataTable, type Column } from "../components/DataTable";
import { Empty, ErrorSurface, Loading } from "../components/Feedback";
import { Metric, Page, Panel } from "../components/Page";
import { MediaStatusBadge, MediaUploadDialog, PresentationMediaDetail, formatBytes, formatDate } from "../components/presentationMedia";
import { useApi } from "../hooks/useApi";
import { useSession } from "../state/session";

type Mode = "central" | "site";
export function PresentationMedia({ mode }: { mode: Mode }) {
  const session = useSession();
  const [eventId, setEventId] = useState("");
  const [filter, setFilter] = useState("all");
  const [upload, setUpload] = useState(false);
  const [selected, setSelected] = useState<PresentationMediaRow | null>(null);
  const [uploadTarget, setUploadTarget] = useState<string>("");
  const events = useApi(async (signal) => mode === "central"
    ? (await centralApi(session.csrfToken).events(signal)).map((event) => ({ id: event.event_id, name: event.name }))
    : (await siteApi.deployments(signal)).map((event) => ({ id: event.central_event_id, name: event.event_name || event.central_event_id })), [mode, session?.csrfToken]);
  const activeEvent = eventId || events.data?.[0]?.id || "";
  const data = useApi(async (signal) => {
    if (!activeEvent) return null;
    if (mode === "site") {
      const [workspace, registration, loose] = await Promise.all([siteApi.mediaWorkspace(activeEvent, signal), siteApi.registration(signal), siteApi.media(signal)]);
      return { rows: workspace.presentations, summary: workspace.summary, imports: [] as CentralMediaImport[], siteId: registration.site_id, unmatched: loose.filter((item) => !item.presentation) };
    }
    const api = centralApi(session.csrfToken);
    const [workspace, presentations] = await Promise.all([api.mediaWorkspace(activeEvent, signal), api.presentations(activeEvent, signal)]);
    return { ...centralRows(workspace.imports, presentations), summary: workspace.summary, imports: workspace.imports, siteId: "", unmatched: workspace.imports.filter((item) => !item.presentation_id) };
  }, [mode, activeEvent, session?.csrfToken]);
  const rows = useMemo(() => (data.data?.rows ?? []).filter((row) => filter === "all" || (filter === "ready" ? row.media_state === "available" : filter === "missing" ? row.media_state === "missing" : filter === "failed" ? row.media_state === "failed" || row.versions.some((version) => version.replication?.state === "failed") : true)), [data.data, filter]);
  const summary = (data.data?.summary ?? {}) as Record<string, number>;
  const doUpload = async (file: File, progress: (value: number) => void, relativePath?: string) => {
    if (mode === "central") await centralApi(session.csrfToken).uploadMedia(activeEvent, file, progress, relativePath);
    else {
      let versionId: string | null = null;
      if (uploadTarget) versionId = (await siteApi.createVersion(uploadTarget)).presentation_version_id;
      await siteApi.uploadMedia(data.data?.siteId || "", activeEvent, file, versionId, progress, relativePath);
    }
    data.refresh();
  };
  if (events.loading) return <Loading />;
  if (events.error) return <ErrorSurface error={events.error} onRetry={events.refresh} />;
  return <Page eyebrow={mode === "central" ? "Global media control" : "Site-local media"} title="Presentation Media" description={mode === "central" ? "Upload, match, version, and track presentation media across Sites." : "Local uploads remain fully available while Central or the WAN is unavailable."} actions={<button className="button button--primary" disabled={!activeEvent} onClick={() => setUpload(true)}>Upload media</button>}>
    {mode === "site" && <div className="autonomy-banner"><strong>Site-local autonomy</strong><span>Uploading and using local media never requires Central connectivity.</span></div>}
    <Panel><div className="media-toolbar"><label>Event<select className="input" value={activeEvent} onChange={(event) => setEventId(event.target.value)}>{events.data?.map((event) => <option value={event.id} key={event.id}>{event.name}</option>)}</select></label><label>Status<select className="input" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All states</option><option value="missing">Missing media</option><option value="ready">Ready</option><option value="failed">Failed</option></select></label><button className="button" onClick={data.refresh}>Refresh status</button></div></Panel>
    {data.loading ? <Loading /> : data.error ? <ErrorSurface error={data.error} onRetry={data.refresh} /> : data.data ? <>
      <div className="metrics"><Metric label="Total presentations" value={summary.expected ?? rows.length} /><Metric label={mode === "central" ? "With media" : "Locally ready"} value={summary.with_media ?? summary.ready ?? 0} tone="success" /><Metric label="Missing media" value={summary.missing ?? 0} tone="warning" /><Metric label="Synchronizing" value={summary.transferring ?? summary.sync_pending ?? 0} /><Metric label="Failures" value={summary.failed ?? 0} tone="danger" /><Metric label="Unmatched uploads" value={data.data.unmatched.length} /></div>
      <Panel title="Presentations" description="Current media and version state. Select Details for complete history."><DataTable rows={rows} columns={columns} rowKey={(row) => row.presentation_id} label="presentation media" pageSize={25} actions={(row) => <><button className="button button--small" onClick={() => setSelected(row)}>Details</button><button className="button button--small" onClick={() => { setUploadTarget(row.presentation_id); setUpload(true); }}>New version</button></>} /></Panel>
      <Panel title="Unmatched / Unassigned Media" description="Files are preserved here until an operator explicitly assigns them.">{data.data.unmatched.length ? <div className="unmatched-grid">{data.data.unmatched.map((item) => <article key={String(item.media_import_id || item.media_object_id)}><strong>{String(item.original_filename || item.filename || "Uploaded media")}</strong><MediaStatusBadge value={item.match_state || item.availability || "unassigned"} /><small>{formatBytes(Number(item.size_bytes) || undefined)}</small>{mode === "central" && <MatchControl item={item as CentralMediaImport} presentations={rows} onDone={data.refresh} csrf={session.csrfToken} />}</article>)}</div> : <Empty title="No unmatched media" />}</Panel>
    </> : <Empty title="Select an event" />}
    {upload && <MediaUploadDialog title={uploadTarget ? "Upload a new presentation version" : "Upload presentation media"} onClose={() => { setUpload(false); setUploadTarget(""); data.refresh(); }} upload={doUpload} />}
    {selected && <PresentationMediaDetail row={selected} onClose={() => setSelected(null)} actions={(version) => mode === "site" && version.replication && ["failed", "retry_wait"].includes(version.replication.state) ? <button className="button button--small" onClick={() => void siteApi.retryReplication(version.replication!.replication_session_id).then(data.refresh)}>Retry Central replication</button> : null} />}
  </Page>;
}

const columns: Column<PresentationMediaRow>[] = [
  { key: "identifier", label: "Identifier", value: (row) => row.presentation_identifier },
  { key: "title", label: "Presentation", value: (row) => row.title },
  { key: "presenter", label: "Presenter(s)", value: (row) => row.presenters },
  { key: "session", label: "Session / room", value: (row) => `${row.session || "—"} · ${row.room || "—"}` },
  { key: "scheduled", label: "Scheduled", value: (row) => row.scheduled_at, render: (row) => formatDate(row.scheduled_at) },
  { key: "version", label: "Current version", value: (row) => row.versions[0]?.version_number, render: (row) => row.versions[0] ? `v${row.versions[0].version_number}` : "—" },
  { key: "filename", label: "Original / canonical filename", value: (row) => row.versions[0]?.media?.original_filename, render: (row) => <><strong>{row.versions[0]?.media?.original_filename || "—"}</strong><small className="block">{row.versions[0]?.media?.canonical_filename || "—"}</small></> },
  { key: "status", label: "Media status", value: (row) => row.media_state, render: (row) => <MediaStatusBadge value={row.media_state} /> },
];

function centralRows(imports: CentralMediaImport[], records: Row[]) {
  const byPresentation = new Map<string, CentralMediaImport[]>();
  imports.forEach((item) => item.presentation_id && byPresentation.set(item.presentation_id, [...(byPresentation.get(item.presentation_id) || []), item]));
  const rows = records.map((record): PresentationMediaRow => {
    const linked = (byPresentation.get(String(record.presentation_id)) || []).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return { presentation_id: String(record.presentation_id), presentation_identifier: String(record.presentation_identifier || record.presentation_code || ""), title: String(record.title || "Untitled presentation"), presenters: Array.isArray(record.presenters) ? record.presenters.map((value) => String((value as Row).display_name || "")).filter(Boolean).join(", ") : "", session: Array.isArray(record.sessions) ? String((record.sessions[0] as Row)?.title || "") : "", room: Array.isArray(record.sessions) ? String((record.sessions[0] as Row)?.location_name || "") : "", scheduled_at: record.scheduled_at as string | undefined, media_state: linked[0]?.import_state === "site_ready" || linked[0]?.import_state === "staged" ? "available" : linked[0]?.import_state || "missing", versions: linked.map((item, index) => ({ presentation_version_id: item.presentation_version_id || item.media_import_id, version_number: linked.length - index, media: { media_object_id: item.media_import_id, original_filename: item.original_filename, canonical_filename: item.canonical_filename, size_bytes: item.size_bytes, sha256: item.sha256, availability: item.import_state, failure_reason: item.error_detail } })) };
  });
  return { rows };
}

function MatchControl({ item, presentations, onDone, csrf }: { item: CentralMediaImport; presentations: PresentationMediaRow[]; onDone: () => void; csrf: string | null }) {
  const [target, setTarget] = useState(""); const [busy, setBusy] = useState(false);
  return <div className="match-control"><select className="input" aria-label={`Match ${item.original_filename}`} value={target} onChange={(event) => setTarget(event.target.value)}><option value="">Select presentation…</option>{presentations.map((row) => <option value={row.presentation_id} key={row.presentation_id}>{row.presentation_identifier} — {row.title}</option>)}</select><button className="button button--small" disabled={!target || busy} onClick={() => { setBusy(true); void centralApi(csrf).assignMedia(item.media_import_id, target).then(onDone).finally(() => setBusy(false)); }}>Confirm assignment</button><small>{item.match_reason || "No deterministic match. Manual confirmation is required."}</small></div>;
}
