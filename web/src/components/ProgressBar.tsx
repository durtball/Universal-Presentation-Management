export type ProgressTone = "active" | "success" | "warning" | "danger" | "neutral";

export function progressTone(status?: string): ProgressTone {
  if (["failed", "exhausted", "integrity_failed"].includes(status || "")) return "danger";
  if (["retry_wait", "warning"].includes(status || "")) return "warning";
  if (["completed", "succeeded", "synced", "site_ready", "available"].includes(status || "")) return "success";
  if (["running", "uploading", "finalizing", "transferring", "syncing", "verifying"].includes(status || "")) return "active";
  return "neutral";
}

export function ProgressBar({ value, max = 100, label, bytes, status, variant = "full", direction, lastProgressAt, indeterminate = false }: {
  value: number; max?: number; label: string; bytes?: { current: number; total: number };
  status?: string; variant?: "compact" | "full"; direction?: "upload" | "delivery" | "replication";
  lastProgressAt?: string | null; indeterminate?: boolean;
}) {
  const safeMax = max > 0 ? max : 100;
  const safeValue = Math.min(safeMax, Math.max(0, value));
  const percent = Math.round(safeValue / safeMax * 100);
  const tone = progressTone(status);
  return <div className={`progress-bar progress-bar--${variant} progress-bar--${tone}${indeterminate ? " progress-bar--indeterminate" : ""}`} data-direction={direction} data-status={status}>
    <div className="progress-bar__labels"><span>{label}</span><strong>{indeterminate ? "Working…" : `${percent}%`}</strong></div>
    <div className="progress-bar__track" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={safeMax} {...(!indeterminate ? { "aria-valuenow": safeValue } : {})}>
      <span className="progress-bar__fill" style={{ width: indeterminate ? "35%" : `${percent}%` }} />
    </div>
    {variant === "full" && (bytes || lastProgressAt) && <small>{bytes ? `${formatBytes(bytes.current)} / ${formatBytes(bytes.total)}` : ""}{bytes && lastProgressAt ? " · " : ""}{lastProgressAt ? `Last progress ${formatRelative(lastProgressAt)}` : ""}</small>}
  </div>;
}

const formatBytes = (value: number) => value < 1024 ? `${value} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KB` : value < 1073741824 ? `${(value / 1048576).toFixed(1)} MB` : `${(value / 1073741824).toFixed(1)} GB`;
const formatRelative = (value: string) => { const seconds = Math.max(0, Math.round((Date.now() - Date.parse(value)) / 1000)); return seconds < 60 ? `${seconds} seconds ago` : `${Math.round(seconds / 60)} minutes ago`; };
