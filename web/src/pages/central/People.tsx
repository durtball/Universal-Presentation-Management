import { useMemo } from "react";
import { centralApi } from "../../api/central";
import type { PersonRecord } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { PageState } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary } from "./Shared";

const columns: Column<PersonRecord>[] = [
  { key: "name", label: "Permanent person", value: (row) => row.display_name },
  { key: "title", label: "Title", value: (row) => row.professional_title },
  {
    key: "organization",
    label: "Organization",
    value: (row) => row.organization,
  },
  { key: "email", label: "Primary email", value: (row) => row.primary_email },
];
export function People() {
  const { adminToken } = useSession();
  const api = useMemo(() => centralApi(adminToken), [adminToken]);
  const result = useApi((signal) => api.people(signal), [api]);
  return (
    <Page
      eyebrow="Identity"
      title="People"
      description="One durable Central identity follows a person across shows and event participation."
    >
      <AdminBoundary>
        <Panel
          title="Permanent identity boundary"
          description="Names are not identity keys. Protected deletion requires dependency review and explicit confirmation; this milestone exposes no deletion shortcut."
        >
          <p className="muted">
            Matching uses stable identifiers and administrator-confirmed
            reconciliation.
          </p>
        </Panel>
        <PageState
          {...result}
          empty={(rows) => !rows.length}
          onRetry={result.refresh}
        >
          {(rows) => (
            <DataTable
              rows={rows}
              columns={columns}
              rowKey={(row) => row.person_id}
              label="People"
            />
          )}
        </PageState>
      </AdminBoundary>
    </Page>
  );
}
