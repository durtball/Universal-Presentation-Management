import { useEffect, useRef, useState, type DragEvent, type ReactNode } from "react";
import type { PresentationMediaRow, PresentationMediaVersion } from "../api/types";
import { StatusBadge } from "./StatusBadge";
import { runBounded, selectPresentationFiles, type SelectedUpload, type SkippedUpload } from "./uploadSelection";

export function MediaStatusBadge({ value }: { value: unknown }) {
  const mapped: Record<string, string> = {
    queued: "transfer_pending", syncing: "synchronizing", synced: "synchronized",
    local_only: "available", needs_review: "review", site_ready: "available",
    transfer_queued: "transfer_pending", integrity_failed: "failed",
  };
  return <StatusBadge value={mapped[String(value)] ?? value} />;
}

type UploadState = "queued" | "uploading" | "retrying" | "staged" | "suggested" | "needs_review" | "confirmed" | "failed";
export interface UploadItem extends SelectedUpload { progress: number; state: UploadState; retryCount: number; error?: string }

export function MediaUploadDialog({ title, onClose, upload }: {
  title: string; onClose: () => void;
  upload: (file: File, progress: (value: number) => void, relativePath?: string, retrying?: (count: number) => void) => Promise<{ state: "staged" | "suggested" | "needs_review" | "confirmed" }>;
}) {
  const input = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [skipped, setSkipped] = useState<SkippedUpload[]>([]);
  const [paused, setPaused] = useState(false);
  const [running, setRunning] = useState(false);
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
      const result = await upload(item.file, (progress) => setItems((all) => all.map((value) => value.id === item.id ? { ...value, progress } : value)), item.relativePath, (retryCount) => setItems((all) => all.map((value) => value.id === item.id ? { ...value, state: "retrying", retryCount } : value)));
      setItems((all) => all.map((value) => value.id === item.id ? { ...value, progress: 100, state: result.state } : value));
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
      <h2 id="upload-title">{title}</h2>
      <p>Original files are streamed to the server and preserved. Replacements create a new version.</p>
      <div className="drop-zone" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
        <strong>Drop presentation files here</strong><span>or</span>
        <div className="button-row upload-choices"><button className="button" onClick={() => input.current?.click()}>Upload Files</button><button className="button" onClick={() => folderInput.current?.click()}>Upload Folder</button></div>
        <input ref={input} hidden type="file" multiple onChange={(event) => event.target.files && add(event.target.files)} />
        <input ref={folderInput} hidden type="file" multiple onChange={(event) => event.target.files && add(event.target.files)} />
      </div>
      {(items.length > 0 || skipped.length > 0) && <div className="upload-summary" aria-label="Upload summary"><span>Discovered <strong>{items.length + skipped.length}</strong></span><span>Eligible <strong>{items.length}</strong></span><span>Queued <strong>{items.filter((item) => item.state === "queued").length}</strong></span><span>Uploading <strong>{items.filter((item) => item.state === "uploading").length}</strong></span><span>Staged <strong>{items.filter((item) => item.state === "staged").length}</strong></span><span>Suggested <strong>{items.filter((item) => item.state === "suggested").length}</strong></span><span>Needs review <strong>{items.filter((item) => item.state === "needs_review").length}</strong></span><span>Confirmed <strong>{items.filter((item) => item.state === "confirmed").length}</strong></span><span>Retrying <strong>{items.filter((item) => item.state === "retrying").length}</strong></span><span>Failed <strong>{items.filter((item) => item.state === "failed").length}</strong></span><span>Skipped <strong>{skipped.length}</strong></span></div>}
      <UploadQueue items={items} retry={run} />
      {skipped.length > 0 && <details className="skipped-files"><summary>{skipped.length} known junk or temporary file{skipped.length === 1 ? "" : "s"} skipped</summary><ul>{skipped.map((item, index) => <li key={`${item.path}-${index}`}><strong>{item.path}</strong> — {item.reason}</li>)}</ul></details>}
      <div className="button-row">
        {items.some((item) => item.state === "queued") && !running && <button className="button button--primary" onClick={() => void start()}>Upload {items.filter((item) => item.state === "queued").length} file{items.filter((item) => item.state === "queued").length === 1 ? "" : "s"}</button>}
        {running && <button className="button" onClick={() => setQueuePaused(!paused)}>{paused ? "Resume" : "Pause"}</button>}
        {items.some((item) => item.state === "failed") && !running && <button className="button" onClick={() => void retryFailed()}>Retry Failed</button>}
        <button className="button" onClick={onClose}>Done</button>
      </div>
    </section>
  </div>;
}

export function UploadQueue({ items, retry }: { items: UploadItem[]; retry: (item: UploadItem) => Promise<void> }) {
  if (!items.length) return null;
  return <div className="upload-queue" aria-label="Upload queue">
    {items.length > 200 && <p>Showing the first 200 of {items.length} files. Aggregate progress includes the complete batch.</p>}
    {items.slice(0, 200).map((item) => <article key={item.id}>
      <div><strong>{item.file.name}</strong><small>{item.relativePath || "Individual file"} · {formatBytes(item.file.size)}</small></div>
      <progress max="100" value={item.progress}>{Math.round(item.progress)}%</progress>
      <MediaStatusBadge value={item.state} />
      {item.retryCount > 0 && <small>Automatic retries: {item.retryCount}</small>}
      {item.error && <p className="error-text">{friendlyError(item.error)}</p>}
      {item.state === "failed" && <button className="button button--small" onClick={() => void retry(item)}>Retry</button>}
    </article>)}
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
    {version.replication && <ReplicationStatus replication={version.replication} />}{actions?.(version)}
  </li>)}</ol>;
}

export function ReplicationStatus({ replication }: { replication: NonNullable<PresentationMediaVersion["replication"]> }) {
  const percent = replication.expected_size ? Math.round(replication.confirmed_offset / replication.expected_size * 100) : 0;
  return <div className="replication"><div><strong>Central replication</strong><MediaStatusBadge value={replication.state} /></div><progress max="100" value={percent}>{percent}%</progress><small>{formatBytes(replication.confirmed_offset)} of {formatBytes(replication.expected_size)} · {percent}%</small>{replication.last_error && <p className="error-text">{friendlyError(replication.last_error)}</p>}</div>;
}

export const formatBytes = (value?: number | null) => value == null ? "—" : value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1048576).toFixed(1)} MB`;
export const formatDate = (value?: string | null) => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
export const friendlyError = (value: string) => /sha|hash|integrity/i.test(value) ? "SHA-256 verification failed. The file was not accepted." : /central|connect/i.test(value) ? "Central is unavailable. Local media remains ready; replication will retry." : value;
