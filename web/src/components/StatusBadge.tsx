const semantics: Record<string, { tone: string; label: string }> = {
  healthy: { tone: "success", label: "Healthy" },
  online: { tone: "success", label: "Online" },
  connected: { tone: "success", label: "Connected" },
  active: { tone: "success", label: "Active" },
  synchronized: { tone: "success", label: "Synchronized" },
  current: { tone: "success", label: "Current" },
  succeeded: { tone: "success", label: "Succeeded" },
  available: { tone: "success", label: "Available" },
  warning: { tone: "warning", label: "Warning" },
  degraded: { tone: "warning", label: "Degraded" },
  pending: { tone: "warning", label: "Pending" },
  running: { tone: "info", label: "Running" },
  retrying: { tone: "warning", label: "Retrying" },
  retry_wait: { tone: "warning", label: "Retry waiting" },
  synchronizing: { tone: "info", label: "Synchronizing" },
  offline: { tone: "neutral", label: "Offline" },
  never_connected: { tone: "neutral", label: "Never connected" },
  unregistered: { tone: "neutral", label: "Unregistered" },
  unsynchronized: { tone: "warning", label: "Unsynchronized" },
  failed: { tone: "danger", label: "Failed" },
  exhausted: { tone: "danger", label: "Failed" },
  unavailable: { tone: "danger", label: "Unavailable" },
  revoked: { tone: "danger", label: "Revoked" },
  disabled: { tone: "neutral", label: "Disabled" },
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
