import { useMemo } from "react";
import { Link } from "react-router-dom";
import { centralApi } from "../../api/central";
import type { EventRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { Page } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const columns: Column<EventRecord>[] = [
  {
    key: "name",
    label: "Event",
    value: (row) => row.name,
    render: (row) => (
      <Link to={`/admin/events/${row.event_id}`}>{row.name}</Link>
    ),
  },
  { key: "timezone", label: "Timezone", value: (row) => row.timezone },
  {
    key: "start",
    label: "Starts",
    value: (row) => row.starts_at,
    render: (row) => when(row.starts_at),
  },
  {
    key: "end",
    label: "Ends",
    value: (row) => row.ends_at,
    render: (row) => when(row.ends_at),
  },
  {
    key: "sites",
    label: "Deployed sites",
    value: (row) => row.deployments.length,
    numeric: true,
  },
];
export function Events() {
  const { adminToken } = useSession();
  const api = useMemo(() => centralApi(adminToken), [adminToken]);
  const result = useApi((signal) => api.events(signal), [api]);
  return (
    <Page
      eyebrow="Program"
      title="Events"
      description="Central-owned event programs and their Site deployments."
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
              rowKey={(row) => row.event_id}
              label="Events"
            />
          )}
        </PageState>
      </AdminBoundary>
    </Page>
  );
}
