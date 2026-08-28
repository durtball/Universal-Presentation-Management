import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import type { CentralMediaImport, PresentationMatchCandidate, PresentationMediaRow, Row } from "../api/types";
import { DataTable, type Column } from "../components/DataTable";
import { Empty, ErrorSurface, Loading } from "../components/Feedback";
import { Metric, Page, Panel } from "../components/Page";
import { MediaStatusBadge, MediaUploadDialog, PresentationMediaDetail, formatBytes, formatDate } from "../components/presentationMedia";
import { useApi } from "../hooks/useApi";
import { useSession } from "../state/session";

type Mode = "central" | "site";
export function PresentationMedia({ mode }: { mode: Mode }) {
  const session = useSession();
  const navigate = useNavigate();
  const [eventId, setEventId] = useState("");
  const [filter, setFilter] = useState("all");
  const [upload, setUpload] = useState(false);
  const [selected, setSelected] = useState<PresentationMediaRow | null>(null);
  const [uploadTarget, setUploadTarget] = useState<string>("");
  const events = useApi(async (signal) => mode === "central"
    ? (await centralApi(session.csrfToken).events(signal)).map((event) => ({ id: event.event_id, name: event.name }))
    : (await siteApi.deployments(signal)).map((event) => ({ id: event.central_event_id, name: event.event_name || event.central_event_id })), [mode, session?.csrfToken]);
  const activeEvent = eventId || events.data?.[0]?.id || "";
  const batches = useApi(async () => mode === "central" && activeEvent ? (await centralApi(session.csrfToken).mediaBatches(activeEvent)).items : [], [mode, activeEvent, session.csrfToken]);
  const data = useApi(async (signal) => {
    if (!activeEvent) return null;
    if (mode === "site") {
      const [workspace, registration, loose] = await Promise.all([siteApi.mediaWorkspace(activeEvent, signal), siteApi.registration(signal), siteApi.media(signal)]);
      return { rows: workspace.presentations, summary: workspace.summary, imports: [] as CentralMediaImport[], candidates: [] as PresentationMatchCandidate[], siteId: registration.site_id, unmatched: loose.filter((item) => !item.presentation) };
    }
    const api = centralApi(session.csrfToken);
    const [workspace, presentations] = await Promise.all([api.mediaWorkspace(activeEvent, signal), api.presentations(activeEvent, signal)]);
    return { ...centralRows(workspace.imports, presentations), summary: workspace.summary, imports: workspace.imports, candidates: [], siteId: "", unmatched: workspace.imports };
  }, [mode, activeEvent, session?.csrfToken]);
  useEffect(() => {
    if (!activeEvent) return;
    const timer = window.setInterval(() => {
      data.poll();
      if (mode === "central") batches.poll();
    }, 10000);
    return () => window.clearInterval(timer);
  }, [activeEvent, mode, data.poll, batches.poll]);
  const rows = useMemo(() => (data.data?.rows ?? []).filter((row) => filter === "all" || (filter === "ready" ? row.media_state === "available" : filter === "missing" ? row.media_state === "missing" : filter === "failed" ? row.media_state === "failed" || row.versions.some((version) => version.replication?.state === "failed") : true)), [data.data, filter]);
  const summary = (data.data?.summary ?? {}) as Record<string, number>;
  const doUpload = async (file: File, progress: (value: number) => void, relativePath?: string, retrying?: (count: number) => void, batchId?: string) => {
    if (mode === "central") {
      const result = await centralApi(session.csrfToken).uploadMedia(activeEvent, file, progress, relativePath, retrying, batchId);
      data.refresh();
      return { state: result.match_state === "suggested" ? "suggested" as const : result.import_state === "needs_review" ? "needs_review" as const : "staged" as const };
    }
    else {
      let versionId: string | null = null;
      if (uploadTarget) versionId = (await siteApi.createVersion(uploadTarget)).presentation_version_id;
      await siteApi.uploadMedia(data.data?.siteId || "", activeEvent, file, versionId, progress, relativePath, retrying);
    }
    data.refresh();
    return { state: uploadTarget ? "confirmed" as const : "needs_review" as const };
  };
  if (events.loading) return <Loading />;
  if (events.error) return <ErrorSurface error={events.error} onRetry={events.refresh} />;
  return <Page eyebrow={mode === "central" ? "Global media control" : "Site-local media"} title="Presentation Media" description={mode === "central" ? "Upload, match, version, and track presentation media across Sites." : "Local uploads remain fully available while Central or the WAN is unavailable."} actions={<button className="button button--primary" disabled={!activeEvent} onClick={() => setUpload(true)}>Upload media</button>}>
    {mode === "site" && <div className="autonomy-banner"><strong>Site-local autonomy</strong><span>Uploading and using local media never requires Central connectivity.</span></div>}
    {mode === "central" && batches.data?.length ? <Panel title="Recent Imports" description="Durable batch accounting remains available after the upload window closes."><div className="log-list">{batches.data.slice(0, 10).map((batch) => <article key={batch.batch_id}><time>{batch.selected_count} selected</time><strong>{batch.registered_count} registered · {batch.failed_count} failed</strong><small>{batch.staged_count} staged · {batch.suggested_count} suggested · {batch.needs_review_count} needs review</small><button className="button button--small" onClick={() => navigate(`/admin/logs?batch_id=${batch.batch_id}`)}>View Batch Log</button></article>)}</div></Panel> : null}
    <Panel><div className="media-toolbar"><label>Event<select className="input" value={activeEvent} onChange={(event) => setEventId(event.target.value)}>{events.data?.map((event) => <option value={event.id} key={event.id}>{event.name}</option>)}</select></label><label>Status<select className="input" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All</option><option value="suggested">Suggested</option><option value="good">Good Matches</option><option value="needs_review">Needs Review</option><option value="confirmed">Confirmed</option><option value="processing">Processing</option><option value="missing">Missing media</option><option value="ready">Ready</option><option value="failed">Failed</option></select></label><button className="button" onClick={data.refresh}>Refresh status</button></div></Panel>
    {data.loading ? <Loading /> : data.error ? <ErrorSurface error={data.error} onRetry={data.refresh} /> : data.data ? <>
      {(summary.expected === 0) && <div className="autonomy-banner autonomy-banner--warning"><strong>No Presentation records available</strong><span>This event currently has no committed Presentation records available for matching. Files will still be preserved for review.</span></div>}
      <div className="metrics"><Metric label="Total presentations" value={summary.expected ?? rows.length} /><Metric label={mode === "central" ? "With media" : "Locally ready"} value={summary.with_media ?? summary.ready ?? 0} tone="success" /><Metric label="Missing media" value={summary.missing ?? 0} tone="warning" /><Metric label="Synchronizing" value={summary.transferring ?? summary.sync_pending ?? 0} /><Metric label="Failures" value={summary.failed ?? 0} tone="danger" /><Metric label="Uploaded files" value={data.data.unmatched.length} /></div>
      <Panel title="Presentations" description="Current media and version state. Select Details for complete history."><DataTable rows={rows} columns={columns} rowKey={(row) => row.presentation_id} label="presentation media" pageSize={25} actions={(row) => <><button className="button button--small" onClick={() => setSelected(row)}>Details</button><button className="button button--small" onClick={() => { setUploadTarget(row.presentation_id); setUpload(true); }}>New version</button></>} /></Panel>
      {mode === "central" ? <CentralReviewQueue initialItems={(data.data.unmatched as CentralMediaImport[]).filter(isIntakeItem)} eventId={activeEvent} csrf={session.csrfToken} /> : null}
    </> : <Empty title="Select an event" />}
    {upload && <MediaUploadDialog title={uploadTarget ? "Upload a new presentation version" : "Bulk Import"} onClose={() => { setUpload(false); setUploadTarget(""); data.refresh(); }} upload={doUpload} registerBatch={mode === "central" ? async (selectedCount, skippedItems) => (await centralApi(session.csrfToken).createMediaBatch(activeEvent, selectedCount, skippedItems)).batch_id : undefined} onViewBatchLog={(batchId) => navigate(`/admin/logs?batch_id=${batchId}`)} />}
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

const REVIEW_PAGE_SIZE = 50;
export type MediaSortKey = "filename" | "file_type" | "size" | "received" | "source" | "status" | "presenter" | "session" | "presentation" | "confidence";
type MediaSort = { key: MediaSortKey; direction: "asc" | "desc" };
export function CentralReviewQueue({ initialItems, eventId, csrf }: { initialItems: CentralMediaImport[]; eventId: string; csrf: string | null }) {
  const [items, setItems] = useState(initialItems.filter(isIntakeItem));
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("needs_review");
  const [fileType, setFileType] = useState("all");
  const [sort, setSort] = useState<MediaSort>({ key: "received", direction: "desc" });
  const [page, setPage] = useState(0);
  const [checked, setChecked] = useState(new Set<string>());
  const [expanded, setExpanded] = useState<string>();
  const [errors, setErrors] = useState(new Map<string, string>());
  const [rescan, setRescan] = useState<{ operation_id: string; complete: number; total: number; finished: boolean }>();
  useEffect(() => setItems(initialItems.filter(isIntakeItem)), [initialItems]);
  useEffect(() => { setChecked((current) => new Set([...current].filter((id) => { const item=items.find((row)=>row.media_import_id===id); return item ? hasValidSuggestion(item) : false; }))); }, [items]);
  useEffect(() => {
    if (!rescan || rescan.finished) return;
    const timer = window.setTimeout(() => void centralApi(csrf).mediaRescanStatus(rescan.operation_id, rescan.complete).then((progress) => {
      setItems((current) => mergeMediaRows(current, progress.items));
      setRescan(progress);
    }).catch(() => undefined), 750);
    return () => window.clearTimeout(timer);
  }, [csrf, rescan]);
  const fileTypes = useMemo(() => [...new Set(items.map(mediaFileType))].sort(), [items]);
  const visible = useMemo(() => items.filter((item) => reviewFilter(item, filter) && (fileType === "all" || mediaFileType(item) === fileType) && reviewVisible(item, search)).sort((left, right) => compareMediaRows(left, right, sort)), [fileType, filter, items, search, sort]);
  const pageRows = visible.slice(page * REVIEW_PAGE_SIZE, (page + 1) * REVIEW_PAGE_SIZE);
  const markConfirmationAccepted = (ids: string[], _state = "confirmation_pending") => { setItems((current) => current.filter((item) => !ids.includes(item.media_import_id))); setChecked((current) => new Set([...current].filter((id) => !ids.includes(id)))); };
  const confirmOne = async (item: CentralMediaImport) => {
    const presentationId = selectedCandidate(item); if (!presentationId) return;
    setErrors((current) => { const next = new Map(current); next.delete(item.media_import_id); return next; });
    try { const result=await centralApi(csrf).assignMedia(item.media_import_id, presentationId); markConfirmationAccepted([item.media_import_id], result.match_state === "confirmed" ? "confirmed" : "confirmation_pending"); }
    catch (error) { setErrors((current) => new Map(current).set(item.media_import_id, error instanceof Error ? error.message : "Confirmation failed")); }
  };
  const confirmSelected = async () => {
    const requested = selectedConfirmations(items, checked);
    if (!requested.length) return;
    const result = await centralApi(csrf).confirmMedia(requested);
    const confirmed = result.results.filter((item) => item.status !== "failed").map((item) => item.media_import_id);
    const failed = result.results.filter((item) => item.status === "failed");
    markConfirmationAccepted(confirmed);
    setErrors((current) => { const next = new Map(current); failed.forEach((item) => next.set(item.media_import_id, item.message || "Confirmation failed")); return next; });
  };
  const rescanOne = async (item: CentralMediaImport) => {
    try { const updated = await centralApi(csrf).refreshMediaMatch(item.media_import_id); setItems((current) => mergeMediaRows(current, [updated])); }
    catch (error) { setErrors((current) => new Map(current).set(item.media_import_id, error instanceof Error ? error.message : "Rescan failed")); }
  };
  const rejectOne = async (item: CentralMediaImport) => {
    const reason = window.prompt("Optional rejection reason") ?? undefined;
    try { await centralApi(csrf).rejectMedia(item.media_import_id, reason); setItems((current) => current.map((row) => row.media_import_id === item.media_import_id ? { ...row, import_state: "rejected", rejection_reason: reason } : row)); }
    catch (error) { setErrors((current) => new Map(current).set(item.media_import_id, error instanceof Error ? error.message : "Rejection failed")); }
  };
  return <Panel title="Presentation Media work queue" description="Compact operator review. Suggestions remain unconfirmed until an operator acts."><div className="media-toolbar media-toolbar--review"><label>Search<input className="input" value={search} onChange={(event) => { setSearch(event.target.value); setPage(0); }} placeholder="Filename, presenter, session, or ID" /></label><label>Filter<select className="input" value={filter} onChange={(event) => { setFilter(event.target.value); setPage(0); }}><option value="needs_review">Needs Review</option><option value="suggested">Suggested Match</option><option value="unmatched">No Match</option><option value="errors">Errors</option><option value="rejected">Rejected</option><option value="all">All</option></select></label><label>File Type<select className="input" value={fileType} onChange={(event) => { setFileType(event.target.value); setPage(0); }}><option value="all">All types</option>{fileTypes.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><button className="button" onClick={() => setChecked(new Set(visible.filter(hasValidSuggestion).map((item) => item.media_import_id)))}>Select All</button><button className="button button--primary" disabled={!checked.size} onClick={() => void confirmSelected()}>Confirm Selected ({checked.size})</button><button className="button" disabled={Boolean(rescan && !rescan.finished)} onClick={() => void centralApi(csrf).rescanUnmatchedMedia(eventId).then((progress) => { setItems((current) => mergeMediaRows(current, progress.items)); setRescan(progress); })}>Rescan All Unmatched</button></div>{rescan && <p role="status">Rescanning unmatched media — {rescan.complete} / {rescan.total}{rescan.finished ? " complete" : ""}</p>}<div className="table-scroll"><table className="data-table"><thead><tr><th></th><SortHeader label="Filename" sortKey="filename" sort={sort} setSort={setSort}/><SortHeader label="File Type" sortKey="file_type" sort={sort} setSort={setSort}/><SortHeader label="File Size" sortKey="size" sort={sort} setSort={setSort}/><SortHeader label="Received" sortKey="received" sort={sort} setSort={setSort}/><SortHeader label="Source" sortKey="source" sort={sort} setSort={setSort}/><SortHeader label="Status" sortKey="status" sort={sort} setSort={setSort}/><SortHeader label="Suggested Presenter" sortKey="presenter" sort={sort} setSort={setSort}/><SortHeader label="Suggested Session" sortKey="session" sort={sort} setSort={setSort}/><SortHeader label="Suggested Presentation" sortKey="presentation" sort={sort} setSort={setSort}/><SortHeader label="Confidence" sortKey="confidence" sort={sort} setSort={setSort}/><th>Evidence / Actions</th></tr></thead><tbody>{pageRows.map((item) => { const candidate=item.suggested_candidate; const id=item.media_import_id; const evidence=item.match_candidates[0]?.evidence ?? []; return <><tr key={id}><td><input type="checkbox" aria-label={`Select ${item.original_filename}`} checked={checked.has(id)} disabled={!hasValidSuggestion(item)} onChange={() => setChecked((current) => { const next=new Set(current); if(next.has(id)) next.delete(id); else next.add(id); return next; })}/></td><td><strong>{item.original_filename}</strong></td><td>{mediaFileType(item)}</td><td>{formatBytes(item.size_bytes)}</td><td>{formatDate(item.created_at)}</td><td>{item.origin}</td><td><MediaStatusBadge value={item.match_state}/></td><td>{candidate?.presenters.map((presenter) => presenter.display_name).join(", ") || "—"}</td><td>{candidate?.session_title || "—"}<small className="block">{candidate?.room || "—"} · {formatDate(candidate?.starts_at)}</small></td><td>{candidate ? `${candidate.presentation_identifier} — ${candidate.title}` : "—"}</td><td>{item.match_candidates[0]?.confidence || "none"}</td><td><small className="block">{evidence.slice(0, 4).join(" · ") || item.match_reason || "No evidence"}</small>{item.import_state === "rejected" ? <strong>Rejected</strong> : <><button className="button button--small" onClick={() => setExpanded(expanded === id ? undefined : id)}>{expanded === id ? "Hide Details" : "Details / Match"}</button><button className="button button--small" onClick={() => void rescanOne(item)}>Rescan</button><button className="button button--small button--primary" disabled={!hasValidSuggestion(item)} onClick={() => void confirmOne(item)}>Confirm</button><button className="button button--small" onClick={() => void rejectOne(item)}>Reject</button></>}{errors.get(id) && <small className="error-text block" role="alert">{errors.get(id)}</small>}</td></tr>{expanded === id && <tr key={`${id}-detail`}><td colSpan={12}><MatchControl item={item} candidates={candidate ? [candidate] : []} eventId={eventId} onDone={() => markConfirmationAccepted([id])} onRescanned={(updated) => setItems((current) => mergeMediaRows(current, [updated]))} onSelectionChange={() => undefined} csrf={csrf} /></td></tr>}</>; })}</tbody></table></div>{!visible.length && <Empty title="No media matches this filter" />}<div className="pagination"><button className="button" disabled={!page} onClick={() => setPage(page-1)}>Previous</button><span>{visible.length ? page*REVIEW_PAGE_SIZE+1 : 0}–{Math.min((page+1)*REVIEW_PAGE_SIZE,visible.length)} of {visible.length}</span><button className="button" disabled={(page+1)*REVIEW_PAGE_SIZE>=visible.length} onClick={() => setPage(page+1)}>Next</button></div></Panel>;
}

export function mergeMediaRows(current: CentralMediaImport[], updates: CentralMediaImport[]) { const byId=new Map(updates.map((item)=>[item.media_import_id,item])); return current.map((item)=>byId.get(item.media_import_id) ?? item); }
export function hasValidSuggestion(item: CentralMediaImport) { return item.match_state === "suggested" && Boolean(item.match_candidates[0]?.presentation_id) && item.suggested_candidate?.presentation_id === item.match_candidates[0].presentation_id; }
export function isIntakeItem(item: CentralMediaImport) { return item.match_state !== "confirmed" && item.import_state !== "assigned" && !item.presentation_id; }
export function mediaFileType(item: CentralMediaImport) { const extension=item.original_filename.split(".").pop()?.toLocaleLowerCase(); return extension && extension !== item.original_filename.toLocaleLowerCase() ? extension.toUpperCase() : (item.mime_type || "Unknown"); }
const confidenceRank: Record<string, number> = { none: 0, low: 1, medium: 2, high: 3 };
export function mediaSortValue(item: CentralMediaImport, key: MediaSortKey): string | number {
  const candidate = item.suggested_candidate;
  if (key === "size") return item.size_bytes ?? -1;
  if (key === "received") return Date.parse(item.created_at) || 0;
  if (key === "confidence") return confidenceRank[item.match_candidates[0]?.confidence || "none"];
  if (key === "filename") return item.original_filename;
  if (key === "file_type") return mediaFileType(item);
  if (key === "source") return item.origin;
  if (key === "status") return item.match_state;
  if (key === "presenter") return candidate?.presenters.map((presenter) => presenter.display_name).join(", ") || "";
  if (key === "session") return candidate?.session_title || "";
  return candidate ? `${candidate.presentation_identifier} ${candidate.title}` : "";
}
export function compareMediaRows(left: CentralMediaImport, right: CentralMediaImport, sort: MediaSort) { const a=mediaSortValue(left,sort.key); const b=mediaSortValue(right,sort.key); const compared=typeof a==="number"&&typeof b==="number"?a-b:String(a).localeCompare(String(b),undefined,{sensitivity:"base"}); return (compared || left.media_import_id.localeCompare(right.media_import_id))*(sort.direction==="asc"?1:-1); }
function SortHeader({ label, sortKey, sort, setSort }: { label: string; sortKey: MediaSortKey; sort: MediaSort; setSort: (value: MediaSort) => void }) { const active=sort.key===sortKey; return <th><button className="sort" type="button" aria-label={`Sort by ${label}`} onClick={() => setSort({key:sortKey,direction:active&&sort.direction==="asc"?"desc":"asc"})}>{label} <span aria-hidden="true">{active?(sort.direction==="asc"?"▲":"▼"):"↕"}</span></button></th>; }
function reviewFilter(item: CentralMediaImport, filter: string) { if(filter==="all") return true; if(filter==="confirmed") return ["confirmed","confirmation_pending"].includes(item.match_state); if(filter==="suggested") return item.match_state==="suggested"; if(filter==="unmatched") return ["unmatched","ambiguous"].includes(item.match_state); if(filter==="errors") return item.import_state==="failed" || Boolean(item.error_detail); if(filter==="rejected") return item.import_state==="rejected"; return !["confirmed","confirmation_pending"].includes(item.match_state) && !["failed","rejected"].includes(item.import_state); }

export function MatchControl({ item, candidates, eventId, onDone, onRescanned, onSelectionChange, csrf }: { item: CentralMediaImport; candidates: PresentationMatchCandidate[]; eventId: string; onDone: () => void; onRescanned?: (item: CentralMediaImport) => void; onSelectionChange?: (presentationId: string) => void; csrf: string | null }) {
  const [target, setTarget] = useState(selectedCandidate(item)); const [busy, setBusy] = useState(false); const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [searchResults, setSearchResults] = useState<PresentationMatchCandidate[]>([]);
  const [resolvedSuggestion, setResolvedSuggestion] = useState<PresentationMatchCandidate | undefined>(
    item.suggested_candidate || candidates.find((candidate) => candidate.presentation_id === selectedCandidate(item)),
  );
  useEffect(() => {
    if (!target || resolvedSuggestion?.presentation_id === target) return;
    const controller = new AbortController();
    void centralApi(csrf).mediaCandidates(eventId, "", controller.signal, [target])
      .then((response) => setResolvedSuggestion(response.candidates[0]))
      .catch(() => undefined);
    return () => controller.abort();
  }, [csrf, eventId, resolvedSuggestion?.presentation_id, target]);
  useEffect(() => {
    if (!search.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void centralApi(csrf).mediaCandidates(eventId, search.trim(), controller.signal)
        .then((response) => setSearchResults(response.candidates))
        .catch(() => undefined);
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [csrf, eventId, search]);
  const visible = search.trim() ? searchResults : resolvedSuggestion
    ? [resolvedSuggestion, ...candidates.filter((candidate) => candidate.presentation_id !== resolvedSuggestion.presentation_id)]
    : candidates;
  const suggestion = resolvedSuggestion;
  const evidence = item.match_candidates.find((candidate) => candidate.presentation_id === suggestion?.presentation_id)?.evidence || [];
  return <div className="match-control"><strong>{item.match_state === "suggested" ? `Suggested — ${item.match_candidates[0]?.confidence || "medium"} confidence` : "Needs Review"}</strong>{suggestion && <span>{candidateLabel(suggestion)}</span>}<label>Search Session / Presenter<input className="input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="ID, presenter, session, title, or room" /></label><select className="input" aria-label={`Match ${item.original_filename}`} value={target} onChange={(event) => { setTarget(event.target.value); onSelectionChange?.(event.target.value); }}><option value="">Choose Session / Presenter…</option>{visible.map((candidate) => <option value={candidate.presentation_id} key={candidate.presentation_id}>{candidateLabel(candidate)}</option>)}</select><div className="match-evidence">{evidence.map((reason) => <small key={reason}>{reason}</small>)}{item.match_reason && <small>{item.match_reason}</small>}</div>{error && <p className="error-text" role="alert">{error}</p>}<div><button className="button button--small button--primary" disabled={!target || busy} onClick={() => { setError(""); setBusy(true); void centralApi(csrf).assignMedia(item.media_import_id, target).then(onDone).catch((reason) => setError(reason instanceof Error ? reason.message : "Confirmation failed")).finally(() => setBusy(false)); }}>Confirm</button><button className="button button--small" disabled={busy} onClick={() => { setError(""); setBusy(true); void centralApi(csrf).refreshMediaMatch(item.media_import_id).then((updated) => onRescanned?.(updated)).catch((reason) => setError(reason instanceof Error ? reason.message : "Rescan failed")).finally(() => setBusy(false)); }}>Rescan</button></div></div>;
}

export function selectedCandidate(item: CentralMediaImport) { return item.match_candidates[0]?.presentation_id || ""; }
export function isGood(item: CentralMediaImport) { return hasValidSuggestion(item) && item.match_candidates[0].confidence === "high" && (item.match_candidates.length === 1 || item.match_candidates[0].score > item.match_candidates[1].score); }
function reviewVisible(item: CentralMediaImport, search: string, filter = "all") { if (filter === "suggested" && item.match_state !== "suggested") return false; if (filter === "good" && !isGood(item)) return false; if (filter === "needs_review" && !["ambiguous", "unmatched"].includes(item.match_state)) return false; if (filter === "confirmed" && item.match_state !== "confirmed") return false; if (filter === "processing" && !["uploading", "staged"].includes(item.import_state)) return false; if (filter === "failed" && item.import_state !== "failed") return false; if (["missing", "ready"].includes(filter)) return false; const value = search.trim().toLocaleLowerCase(); return !value || `${item.original_filename} ${item.presentation_identifier || ""} ${item.match_reason || ""} ${item.suggested_candidate?.title || ""} ${item.suggested_candidate?.session_title || ""} ${item.suggested_candidate?.presenters.map((presenter) => presenter.display_name).join(" ") || ""}`.toLocaleLowerCase().includes(value); }
function candidateLabel(candidate: PresentationMatchCandidate) { const presenter = candidate.presenters.map((item) => item.display_name).join(", ") || "No presenter"; return `${candidate.presentation_identifier} — ${presenter} — ${candidate.session_title || candidate.title} — ${candidate.room || "No room"}${candidate.starts_at ? ` — ${formatDate(candidate.starts_at)}` : ""}`; }

export function goodMatchIds(items: CentralMediaImport[], search = "", filter = "all") { return items.filter((item) => isGood(item) && reviewVisible(item, search, filter)).map((item) => item.media_import_id); }

export function selectedConfirmations(items: CentralMediaImport[], selected: Set<string>, overrides = new Map<string, string>()) {
  return items.filter((item) => selected.has(item.media_import_id) && !["confirmed","confirmation_pending"].includes(item.match_state) && (overrides.has(item.media_import_id) || hasValidSuggestion(item))).map((item) => ({ media_import_id: item.media_import_id, presentation_id: overrides.get(item.media_import_id) || selectedCandidate(item) })).filter((item) => Boolean(item.presentation_id));
}
