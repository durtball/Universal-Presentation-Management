import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode, type UIEvent } from "react";
import type { PresentationMediaRow, PresentationMediaVersion } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { ProgressBar } from "./ProgressBar";
import { runBounded, selectPresentationFiles, type SelectedUpload, type SkippedUpload } from "./uploadSelection";

export function MediaStatusBadge({ value }: { value: unknown }) {
  const mapped: Record<string, string> = {
    queued: "transfer_pending", syncing: "synchronizing", synced: "synchronized",
    local_only: "available", needs_review: "review", site_ready: "available",
    transfer_queued: "transfer_pending", integrity_failed: "failed",
  };
  return <StatusBadge value={mapped[String(value)] ?? value} />;
}

type UploadState = "queued" | "uploading" | "finalizing" | "retrying" | "staged" | "intake_ready" | "suggested" | "needs_review" | "confirmed" | "failed";
type UploadResult = { state: "staged" | "intake_ready" | "suggested" | "needs_review" | "confirmed"; sha256?: string | null; sizeBytes?: number | null; availability?: string | null; failureReason?: string | null };
export interface UploadItem extends SelectedUpload { progress: number; state: UploadState; retryCount: number; error?: string; result?: UploadResult }

export function MediaUploadDialog({ title, onClose, upload, registerBatch, onViewBatchLog }: {
  title: string; onClose: () => void;
  upload: (file: File, progress: (value: number) => void, relativePath?: string, retrying?: (count: number) => void, batchId?: string) => Promise<UploadResult>;
  registerBatch?: (selected: number, skipped: SkippedUpload[]) => Promise<string>;
  onViewBatchLog?: (batchId: string) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [skipped, setSkipped] = useState<SkippedUpload[]>([]);
  const [paused, setPaused] = useState(false);
  const [running, setRunning] = useState(false);
  const [batchId, setBatchId] = useState<string>();
  const batchIdRef = useRef<string | undefined>(undefined);
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const pausedRef = useRef(false);
  const resumeWaiters = useRef<Array<() => void>>([]);
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
    folderInput.current?.setAttribute("directory", "");
  }, []);
  const add = (files: FileList | File[]) => {
    const selection = selectPresentationFiles(Array.from(files));
    setItems((current) => [...current, ...selection.accepted.map((item) => ({ ...item, progress: 0, state: "queued" as const, retryCount: 0 }))]);
    setSkipped((current) => [...current, ...selection.skipped]);
  };
  const run = async (item: UploadItem) => {
    setItems((all) => all.map((value) => value.id === item.id ? { ...value, state: "uploading", error: undefined } : value));
    try {
      const result = await upload(item.file, (progress) => setItems((all) => all.map((value) => value.id === item.id ? { ...value, progress, state: progress >= 100 ? "finalizing" : "uploading" } : value)), item.relativePath, (retryCount) => setItems((all) => all.map((value) => value.id === item.id ? { ...value, state: "retrying", retryCount } : value)), batchIdRef.current);
      setItems((all) => all.map((value) => value.id === item.id ? { ...value, progress: 100, state: result.state, result } : value));
    } catch (error) {
      setItems((all) => all.map((value) => value.id === item.id ? { ...value, state: "failed", error: error instanceof Error ? error.message : "Upload failed" } : value));
    }
  };
  const waitIfPaused = () => pausedRef.current ? new Promise<void>((resolve) => resumeWaiters.current.push(resolve)) : Promise.resolve();
  const setQueuePaused = (value: boolean) => {
    pausedRef.current = value; setPaused(value);
    if (!value) resumeWaiters.current.splice(0).forEach((resolve) => resolve());
  };
  const start = async () => {
    if (running) return;
    setRunning(true);
    await runBounded(items.filter((item) => item.state === "queued"), run, undefined, waitIfPaused);
    setRunning(false);
  };
  const retryFailed = async () => {
    const failed = items.filter((item) => item.state === "failed");
    setItems((all) => all.map((item) => item.state === "failed" ? { ...item, state: "queued", error: undefined } : item));
    setRunning(true);
    await runBounded(failed, run, undefined, waitIfPaused);
    setRunning(false);
  };
  const drop = (event: DragEvent) => { event.preventDefault(); add(event.dataTransfer.files); };
  return <div className="dialog-backdrop" role="presentation">
    <section className="dialog media-upload" role="dialog" aria-modal="true" aria-labelledby="upload-title">
      <h2 id="upload-title">{title} — {items.length + skipped.length} files</h2>
      <p>Original files are streamed to the server and preserved. Replacements create a new version.</p>
      <div className="drop-zone" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
        <strong>Drop presentation files here</strong><span>or</span>
        <div className="button-row upload-choices"><button className="button" onClick={() => input.current?.click()}>Upload Files</button><button className="button" onClick={() => folderInput.current?.click()}>Upload Folder</button></div>
        <input ref={input} hidden type="file" multiple onChange={(event) => event.target.files && add(event.target.files)} />
        <input ref={folderInput} hidden type="file" multiple onChange={(event) => event.target.files && add(event.target.files)} />
      </div>
      {(items.length > 0 || skipped.length > 0) && <div className="upload-summary" aria-label="Upload summary"><span>Selected <strong>{items.length + skipped.length}</strong></span><span>Registered <strong>{batchId ? items.length + skipped.length : 0}</strong></span><span>Queued <strong>{items.filter((item) => item.state === "queued").length}</strong></span><span>Uploading <strong>{items.filter((item) => item.state === "uploading").length}</strong></span><span>Finalizing / verifying <strong>{items.filter((item) => item.state === "finalizing").length}</strong></span><span>Intake ready <strong>{items.filter((item) => ["staged", "intake_ready"].includes(item.state)).length}</strong></span><span>Suggested <strong>{items.filter((item) => item.state === "suggested").length}</strong></span><span>Needs review <strong>{items.filter((item) => item.state === "needs_review").length}</strong></span><span>Confirmed <strong>{items.filter((item) => item.state === "confirmed").length}</strong></span><span>Retrying <strong>{items.filter((item) => item.state === "retrying").length}</strong></span><span>Failed <strong>{items.filter((item) => item.state === "failed").length}</strong></span><span>Skipped <strong>{skipped.length}</strong></span></div>}
      <div className="media-toolbar"><input className="input" aria-label="Search batch files" placeholder="Search filename or relative path" value={search} onChange={(event) => setSearch(event.target.value)} /><select className="input" aria-label="Filter batch files" value={filter} onChange={(event) => setFilter(event.target.value)}>{["all","queued","uploading","finalizing","intake_ready","staged","suggested","needs_review","confirmed","retrying","failed","skipped"].map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></div>
      <UploadQueue items={items} retry={run} filter={filter} search={search} />
      {skipped.length > 0 && ["all", "skipped"].includes(filter) && <details className="skipped-files" open={filter === "skipped"}><summary>{skipped.length} known junk or temporary file{skipped.length === 1 ? "" : "s"} skipped</summary><ul>{skipped.filter((item) => item.path.toLocaleLowerCase().includes(search.toLocaleLowerCase())).map((item, index) => <li key={`${item.path}-${index}`}><strong>{item.path}</strong> — {item.reason}</li>)}</ul></details>}
      <div className="button-row">
        {items.some((item) => item.state === "queued") && !running && <button className="button button--primary" onClick={() => void (async () => { if (!batchId && registerBatch) { const value = await registerBatch(items.length + skipped.length, skipped); batchIdRef.current = value; setBatchId(value); } await start(); })()}>Upload {items.filter((item) => item.state === "queued").length} file{items.filter((item) => item.state === "queued").length === 1 ? "" : "s"}</button>}
        {running && <button className="button" onClick={() => setQueuePaused(!paused)}>{paused ? "Resume" : "Pause"}</button>}
        {items.some((item) => item.state === "failed") && !running && <button className="button" onClick={() => void retryFailed()}>Retry Failed</button>}
        {batchId && onViewBatchLog && <button className="button" onClick={() => onViewBatchLog(batchId)}>View Batch Log</button>}
        {batchId && onViewBatchLog && <button className="button" onClick={() => window.open(`/api/v1/admin/logs?batch_id=${batchId}&minutes=525600&limit=500`, "_blank")}>Export Batch Log</button>}
        <button className="button" onClick={onClose}>Done</button>
      </div>
    </section>
  </div>;
}

export function UploadQueue({ items, retry, filter = "all", search = "" }: { items: UploadItem[]; retry: (item: UploadItem) => Promise<void>; filter?: string; search?: string }) {
  const [scrollTop, setScrollTop] = useState(0);
  const filtered = useMemo(() => items.filter((item) => filter !== "skipped" && (filter === "all" || item.state === filter) && `${item.file.name} ${item.relativePath || ""}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())), [items, filter, search]);
  if (!items.length) return null;
  const rowHeight = 58, viewport = 348, overscan = 4;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
  const end = Math.min(filtered.length, Math.ceil((scrollTop + viewport) / rowHeight) + overscan);
  return <div className="upload-queue" aria-label="Upload queue" onScroll={(event: UIEvent<HTMLDivElement>) => setScrollTop(event.currentTarget.scrollTop)}>
    <div style={{ height: start * rowHeight }} aria-hidden="true" />
    {filtered.slice(start, end).map((item) => <article key={item.id} data-index={items.indexOf(item)}>
      <div><strong>{item.file.name}</strong><small>{item.relativePath || "Individual file"} · {formatBytes(item.file.size)}</small></div>
      <ProgressBar value={item.progress} label={`${item.file.name} upload progress`} status={item.state} variant="compact" direction="upload" />
      <MediaStatusBadge value={item.state} />
      {item.result?.sha256 && <small title={item.result.sha256}>SHA-256 {item.result.sha256.slice(0, 16)}…</small>}
      {item.result?.sizeBytes != null && <small>Accepted {formatBytes(item.result.sizeBytes)}</small>}
      {item.retryCount > 0 && <small>Automatic retries: {item.retryCount}</small>}
      {item.error && <p className="error-text">{friendlyError(item.error)}</p>}
      {item.state === "failed" && <button className="button button--small" onClick={() => void retry(item)}>Retry</button>}
    </article>)}
    <div style={{ height: (filtered.length - end) * rowHeight }} aria-hidden="true" />
  </div>;
}

export function PresentationMediaDetail({ row, onClose, actions }: { row: PresentationMediaRow; onClose: () => void; actions?: (version: PresentationMediaVersion) => ReactNode }) {
  return <div className="dialog-backdrop"><section className="dialog media-detail" role="dialog" aria-modal="true" aria-labelledby="media-detail-title">
    <header><div><span className="eyebrow">{row.presentation_identifier || "No identifier"}</span><h2 id="media-detail-title">{row.title}</h2></div><button className="button" onClick={onClose}>Close</button></header>
    <dl className="detail-grid"><div><dt>Presenter(s)</dt><dd>{row.presenters || "—"}</dd></div><div><dt>Session</dt><dd>{row.session || "—"}</dd></div><div><dt>Room</dt><dd>{row.room || "—"}</dd></div><div><dt>Scheduled</dt><dd>{formatDate(row.scheduled_at)}</dd></div></dl>
    <h3>Version history</h3>
    <PresentationVersionList versions={row.versions} actions={actions} />
  </section></div>;
}

export function PresentationVersionList({ versions, actions }: { versions: PresentationMediaVersion[]; actions?: (version: PresentationMediaVersion) => ReactNode }) {
  if (!versions.length) return <p className="empty-inline">No media versions have been created.</p>;
  return <ol className="version-list">{versions.map((version, index) => <li key={version.presentation_version_id}>
    <header><strong>Version {version.version_number}</strong>{index === 0 && <span className="status status--info">Current</span>}<MediaStatusBadge value={version.media?.availability || "missing"} /></header>
    <dl className="detail-grid"><div><dt>Original filename</dt><dd>{version.media?.original_filename || "—"}</dd></div><div><dt>UPM canonical filename</dt><dd>{version.media?.canonical_filename || "—"}</dd></div><div><dt>Size</dt><dd>{formatBytes(version.media?.size_bytes)}</dd></div><div><dt>SHA-256</dt><dd><code title={version.media?.sha256 || ""}>{version.media?.sha256 ? `${version.media.sha256.slice(0, 16)}…` : "—"}</code></dd></div></dl>
    {version.media?.provenance && <details className="media-provenance"><summary>Origin and provenance</summary><dl className="detail-grid"><div><dt>Origin</dt><dd>{version.media.provenance.origin === "smb" ? "SMB Incoming" : "Browser upload"}</dd></div><div><dt>Source share</dt><dd>{version.media.provenance.source_share || "—"}</dd></div><div><dt>Source path</dt><dd>{version.media.provenance.source_relative_path || version.media.original_filename}</dd></div><div><dt>Received</dt><dd>{formatDate(version.media.provenance.received_at)}</dd></div></dl></details>}
    <div className="transfer-status-grid"><LocalMediaStatus version={version} />{version.delivery && <DeliveryStatus delivery={version.delivery} />}{version.replication && <ReplicationStatus replication={version.replication} />}</div>{actions?.(version)}
  </li>)}</ol>;
}

export function ReplicationStatus({ replication }: { replication: NonNullable<PresentationMediaVersion["replication"]> }) {
  return <div className="replication transfer-status"><div><strong>Site → Central replication</strong><MediaStatusBadge value={replication.state} /></div><ProgressBar value={replication.confirmed_offset} max={replication.expected_size || 1} label="Replication to Central" bytes={{current:replication.confirmed_offset,total:replication.expected_size}} status={replication.state} direction="replication" lastProgressAt={replication.last_progress_at} />{replication.retry_count > 0 && <small>Retry {replication.retry_count}</small>}{replication.last_error && <p className="error-text">{friendlyError(replication.last_error)} Local media remains ready and usable.</p>}</div>;
}

export function DeliveryStatus({ delivery }: { delivery: NonNullable<PresentationMediaVersion["delivery"]> }) {
  const verified = delivery.state === "completed";
  const progressValue = !verified && delivery.expected_size > 0 && delivery.confirmed_offset >= delivery.expected_size ? delivery.expected_size * .99 : delivery.confirmed_offset;
  return <div className="delivery transfer-status"><div><strong>Central → Site delivery</strong><MediaStatusBadge value={delivery.state} /></div><ProgressBar value={progressValue} max={delivery.expected_size || 1} label="Download from Central" bytes={{current:delivery.confirmed_offset,total:delivery.expected_size}} status={delivery.state} direction="delivery" lastProgressAt={delivery.last_progress_at} />{delivery.retry_count > 0 && <small>Retry {delivery.retry_count}</small>}{delivery.error_detail && <p className="error-text">{friendlyError(delivery.error_detail)}{delivery.state === "failed" ? " No verified local copy is available yet." : ""}</p>}</div>;
}

function LocalMediaStatus({ version }: { version: PresentationMediaVersion }) { return <div className="transfer-status"><div><strong>Local media</strong><MediaStatusBadge value={version.media?.availability || "missing"} /></div>{version.media?.failure_reason && <p className="error-text">{version.media.failure_reason}</p>}</div>; }

export function TransferActivity({ title, transfers }: { title: string; transfers: Array<{confirmed_offset:number;expected_size:number;state:string}> }) {
  const relevant=transfers.filter((item)=>!["completed","synced","cancelled","expired"].includes(item.state));
  if (!relevant.length) return null;
  const known=relevant.filter((item)=>item.expected_size>0); const current=known.reduce((sum,item)=>sum+item.confirmed_offset,0); const total=known.reduce((sum,item)=>sum+item.expected_size,0);
  return <section className="transfer-activity" aria-label={title}><div><strong>{title}</strong><small>{relevant.filter((item)=>["transferring","syncing","verifying"].includes(item.state)).length} active · {relevant.filter((item)=>["queued","available","pending","retry_wait"].includes(item.state)).length} queued/retry · {relevant.filter((item)=>["failed","exhausted"].includes(item.state)).length} failed</small></div>{total>0&&<ProgressBar value={current} max={total} label={`${title} aggregate progress`} bytes={{current,total}} status={relevant.some((item)=>item.state==="failed")?"failed":"transferring"} variant="compact" />}</section>;
}

export const formatBytes = (value?: number | null) => value == null ? "—" : value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`;
export const formatDate = (value?: string | null) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
export const friendlyError = (value: string) => /sha|hash|integrity/i.test(value) ? "SHA-256 verification failed. The file was not accepted." : /central|connect/i.test(value) ? "Central is unavailable. Local media remains ready; replication will retry." : value;
