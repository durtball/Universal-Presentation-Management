import { useMemo, useState, type FormEvent } from "react";
import { centralApi } from "../../api/central";
import type { Row } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EventPicker } from "../../components/EventPicker";
import { Empty, ErrorSurface, Loading } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

const columns: Column<Row>[] = [
  {
    key: "name",
    label: "Source file",
    value: (row) => String(row.original_filename ?? row.filename ?? ""),
  },
  {
    key: "status",
    label: "Status",
    value: (row) => row.status,
    render: (row) => <StatusBadge value={row.status} />,
  },
  {
    key: "rows",
    label: "Rows",
    value: (row) => row.row_count ?? row.total_rows,
    numeric: true,
  },
  {
    key: "created",
    label: "Created",
    value: (row) => row.created_at,
    render: (row) => when(row.created_at),
  },
];
export function Imports() {
  const { adminToken } = useSession();
  const api = useMemo(() => centralApi(adminToken), [adminToken]);
  const events = useApi((signal) => api.events(signal), [api]);
  const [eventId, setEventId] = useState("");
  const selected = eventId || events.data?.[0]?.event_id || "";
  const batches = useApi(
    (signal) =>
      selected ? api.imports(selected, signal) : Promise.resolve([]),
    [api, selected],
  );
  const [file, setFile] = useState<File>();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !selected) return;
    setUploading(true);
    setUploadError(undefined);
    try {
      await api.uploadImport(selected, file);
      setFile(undefined);
      (event.currentTarget as HTMLFormElement).reset();
      batches.refresh();
    } catch (error) {
      setUploadError(error);
    } finally {
      setUploading(false);
    }
  };
  return (
    <Page
      eyebrow="Ingestion"
      title="Imports"
      description="CSV/XLSX staging, validation, identity reconciliation, preview, and transactional commit."
    >
      <AdminBoundary>
        {events.loading ? (
          <Loading />
        ) : events.error ? (
          <ErrorSurface error={events.error} />
        ) : !events.data?.length ? (
          <Empty title="No event available" />
        ) : (
          <>
            <EventPicker
              events={events.data}
              value={selected}
              onChange={setEventId}
            />
            <Panel
              title="Stage an import"
              description="Current parsing is synchronous and size-limited; worker handoff remains a documented future extension."
            >
              <form className="inline-form" onSubmit={submit}>
                <label className="field">
                  <span>CSV or XLSX file</span>
                  <input
                    className="input"
                    type="file"
                    accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    required
                    onChange={(event) => setFile(event.target.files?.[0])}
                  />
                </label>
                <button
                  className="button button--primary"
                  disabled={!file || uploading}
                >
                  {uploading ? "Staging…" : "Upload and stage"}
                </button>
              </form>
              {uploadError != null ? <ErrorSurface error={uploadError} /> : null}
            </Panel>
            {batches.loading ? (
              <Loading />
            ) : batches.error ? (
              <ErrorSurface error={batches.error} onRetry={batches.refresh} />
            ) : (
              <DataTable
                rows={batches.data ?? []}
                columns={columns}
                rowKey={(row) => String(row.import_batch_id)}
                label="Import batches"
              />
            )}
          </>
        )}
      </AdminBoundary>
    </Page>
  );
}
