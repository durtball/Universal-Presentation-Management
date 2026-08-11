import { useMemo, useState, type FormEvent } from "react";
import { centralApi } from "../../api/central";
import type { ImportBatch } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EventPicker } from "../../components/EventPicker";
import { Empty, ErrorSurface, Loading } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary, when } from "./Shared";

function reviewInformation(batch: ImportBatch) {
  if (batch.failure_summary) return batch.failure_summary;
  if (batch.status !== "review") return undefined;
  const validationRows = Math.max(
    0,
    batch.row_count - batch.valid_count - batch.warning_count,
  );
  const details = [
    validationRows ? `${validationRows} validation issue rows` : undefined,
    batch.warning_count ? `${batch.warning_count} warnings` : undefined,
    batch.conflict_count ? `${batch.conflict_count} conflicts` : undefined,
  ].filter(Boolean);
  return details.join(", ") || "Review required";
}

const columns: Column<ImportBatch>[] = [
  {
    key: "name",
    label: "Source file",
    value: (row) => row.filename,
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
    value: (row) => row.row_count,
    numeric: true,
  },
  {
    key: "valid",
    label: "Valid",
    value: (row) => row.valid_count,
    numeric: true,
  },
  {
    key: "warnings",
    label: "Warnings",
    value: (row) => row.warning_count,
    numeric: true,
  },
  {
    key: "conflicts",
    label: "Conflicts",
    value: (row) => row.conflict_count,
    numeric: true,
  },
  {
    key: "rejected",
    label: "Rejected",
    value: (row) => row.rejected_count,
    numeric: true,
  },
  {
    key: "review",
    label: "Review information",
    value: reviewInformation,
    sortable: false,
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
  const [stagedBatch, setStagedBatch] = useState<ImportBatch>();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || !selected) return;
    const form = event.currentTarget as HTMLFormElement;
    setUploading(true);
    setUploadError(undefined);
    setStagedBatch(undefined);
    let batch: ImportBatch;
    try {
      batch = await api.uploadImport(selected, file);
    } catch (error) {
      setUploadError(error);
      setUploading(false);
      return;
    }
    setStagedBatch(batch);
    setFile(undefined);
    form.reset();
    batches.refresh();
    setUploading(false);
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
              {stagedBatch ? (
                <p className="success-message" role="status">
                  <strong>Import staged successfully.</strong>{" "}
                  {stagedBatch.filename} is {stagedBatch.status} with{" "}
                  {stagedBatch.row_count} rows.
                </p>
              ) : null}
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
