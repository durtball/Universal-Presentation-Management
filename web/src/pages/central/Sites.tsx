import { useMemo, useState } from "react";
import { centralApi } from "../../api/central";
import type { SiteRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { Page } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const columns: Column<SiteRecord>[] = [
  { key: "name", label: "Site", value: (row) => row.display_name },
  {
    key: "identity",
    label: "Identity",
    value: (row) => row.site_id,
    render: (row) => <code>{row.site_id.slice(0, 8)}…</code>,
  },
  {
    key: "enrollment",
    label: "Enrollment",
    value: (row) => row.enrollment_state,
    render: (row) => <StatusBadge value={row.enrollment_state} />,
  },
  {
    key: "connectivity",
    label: "Connectivity",
    value: (row) => row.connectivity,
    render: (row) => <StatusBadge value={row.connectivity} />,
  },
  {
    key: "last",
    label: "Last contact",
    value: (row) => row.last_seen_at,
    render: (row) => when(row.last_seen_at),
  },
  {
    key: "sync",
    label: "Sync",
    value: (row) =>
      row.failed_sync
        ? "failed"
        : row.pending_sync
          ? "synchronizing"
          : "synchronized",
    render: (row) => (
      <StatusBadge
        value={
          row.failed_sync
            ? "failed"
            : row.pending_sync
              ? "synchronizing"
              : "synchronized"
        }
      />
    ),
  },
];
export function Sites() {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const result = useApi((signal) => api.sites(signal), [api]);
  const [busy, setBusy] = useState<string>();
  return (
    <Page
      eyebrow="Global registry"
      title="Sites"
      description="Enrollment, connectivity, health, and synchronization across independently deployed Sites."
    >
      <AdminBoundary>
        <PageState
          {...result}
          empty={(rows) => !rows.length}
          onRetry={result.refresh}
        >
          {(rows) => (
            <DataTable
              rows={rows}
              columns={columns}
              rowKey={(row) => row.site_id}
              label="Sites"
              actions={(row) =>
                row.enrollment_state === "pending" ? (
                  <button
                    className="button button--small"
                    disabled={busy === row.site_id}
                    onClick={async () => {
                      setBusy(row.site_id);
                      try {
                        await api.changeSiteState(row.site_id, "approve");
                        result.refresh();
                      } finally {
                        setBusy(undefined);
                      }
                    }}
                  >
                    Approve
                  </button>
                ) : null
              }
            />
          )}
        </PageState>
      </AdminBoundary>
    </Page>
  );
}
