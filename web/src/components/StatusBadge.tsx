const semantics: Record<string, { tone: string; label: string }> = {
  healthy: { tone: "success", label: "Healthy" },
  online: { tone: "success", label: "Online" },
  connected: { tone: "success", label: "Connected" },
  active: { tone: "success", label: "Active" },
  synchronized: { tone: "success", label: "Synchronized" },
  current: { tone: "success", label: "Current" },
  succeeded: { tone: "success", label: "Succeeded" },
  available: { tone: "success", label: "Available" },
  ready: { tone: "success", label: "Ready" },
  committed: { tone: "success", label: "Committed" },
  warning: { tone: "warning", label: "Warning" },
  degraded: { tone: "warning", label: "Degraded" },
  pending: { tone: "warning", label: "Pending" },
  uploaded: { tone: "info", label: "Uploaded" },
  processing: { tone: "info", label: "Processing" },
  transfer_pending: { tone: "warning", label: "Transfer pending" },
  transferring: { tone: "info", label: "Transferring" },
  missing: { tone: "warning", label: "Missing" },
  error: { tone: "danger", label: "Error" },
  parsing: { tone: "info", label: "Parsing" },
  staged: { tone: "info", label: "Staged" },
  review: { tone: "warning", label: "Review" },
  committing: { tone: "info", label: "Committing" },
  running: { tone: "info", label: "Running" },
  retrying: { tone: "warning", label: "Retrying" },
  retry_wait: { tone: "warning", label: "Retry waiting" },
  synchronizing: { tone: "info", label: "Synchronizing" },
  offline: { tone: "neutral", label: "Offline" },
  never_connected: { tone: "neutral", label: "Never connected" },
  unregistered: { tone: "neutral", label: "Unregistered" },
  unassigned: { tone: "warning", label: "Unassigned" },
  unknown: { tone: "neutral", label: "Status unavailable" },
  not_enrolled: { tone: "neutral", label: "Not enrolled" },
  not_started: { tone: "neutral", label: "Not started" },
  not_required: { tone: "neutral", label: "Not required" },
  unsynchronized: { tone: "warning", label: "Unsynchronized" },
  failed: { tone: "danger", label: "Failed" },
  exhausted: { tone: "danger", label: "Failed" },
  unavailable: { tone: "danger", label: "Unavailable" },
  revoked: { tone: "danger", label: "Revoked" },
  disabled: { tone: "neutral", label: "Disabled" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

export function statusInfo(value: unknown) {
  const key = String(value ?? "unknown").toLowerCase();
  return (
    semantics[key] ?? {
      tone: "neutral",
      label: key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase()),
    }
  );
}
export function StatusBadge({ value }: { value: unknown }) {
  const status = statusInfo(value);
  return (
    <span className={`status status--${status.tone}`}>
      <span aria-hidden="true" className="status__dot" />
      {status.label}
    </span>
  );
}
