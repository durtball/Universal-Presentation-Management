import { useMemo, useState, type FormEvent } from "react";
import { centralApi } from "../../api/central";
import type { ImportBatch, ImportRow } from "../../api/types";
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
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
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
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const detail = useApi(
    (signal) =>
      selectedBatchId
        ? api.importDetail(selectedBatchId, signal)
        : Promise.resolve(undefined),
    [api, selectedBatchId],
  );
  const [actionError, setActionError] = useState<unknown>();
  const [committing, setCommitting] = useState(false);
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
    setSelectedBatchId(String(batch.import_batch_id));
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
                actions={(row) => (
                  <button
                    className="button button--small"
                    onClick={() => setSelectedBatchId(String(row.import_batch_id))}
                  >
                    Review
                  </button>
                )}
              />
            )}
            {selectedBatchId ? (
              <Panel title="Import review" description="Detected columns, source samples, validation, identity reconciliation, preview, and commit use the persisted staging record.">
                {detail.loading ? <Loading /> : detail.error ? (
                  <ErrorSurface error={detail.error} onRetry={detail.refresh} />
                ) : detail.data ? (
                  <ImportReview
                    batch={detail.data}
                    busy={committing}
                    onResolve={async (row, action, personId) => {
                      setActionError(undefined);
                      try {
                        await api.reconcileImportRow(row.import_row_id, action, personId);
                        detail.refresh();
                        batches.refresh();
                      } catch (error) { setActionError(error); }
                    }}
                    onCommit={async () => {
                      setCommitting(true);
                      setActionError(undefined);
                      try {
                        await api.commitImport(selectedBatchId);
                        detail.refresh();
                        batches.refresh();
                      } catch (error) { setActionError(error); }
                      finally { setCommitting(false); }
                    }}
                  />
                ) : null}
                {actionError != null ? <ErrorSurface error={actionError} /> : null}
              </Panel>
            ) : null}
          </>
        )}
      </AdminBoundary>
    </Page>
  );
}

function ImportReview({
  batch,
  busy,
  onResolve,
  onCommit,
}: {
  batch: ImportBatch;
  busy: boolean;
  onResolve: (row: ImportRow, action: string, personId?: string) => Promise<void>;
  onCommit: () => Promise<void>;
}) {
  const counts = batch.preview_counts ?? {};
  return (
    <div className="review-stack">
      <div className="metrics">
        {Object.entries(counts).map(([key, value]) => (
          <div className="metric" key={key}>
            <span>{key.replaceAll("_", " ")}</span><strong>{value}</strong>
          </div>
        ))}
      </div>
      <div>
        <h3>Detected column mapping</h3>
        <div className="mapping-grid">
          {Object.entries(batch.detected_mapping ?? {}).map(([source, target]) => (
            <div key={source}><code>{source}</code><span>→</span><code>{target}</code></div>
          ))}
        </div>
      </div>
      <div>
        <h3>Source sample</h3>
        <pre className="source-sample">{JSON.stringify(batch.sample_rows ?? [], null, 2)}</pre>
      </div>
      <DataTable
        rows={batch.rows ?? []}
        columns={[
          { key: "row", label: "Source row", value: (row) => row.source_row_number, numeric: true },
          { key: "program", label: "Program ID", value: (row) => String(row.normalized_values.session_code ?? "—") },
          { key: "presenter", label: "Presenter", value: (row) => String(row.normalized_values.display_name ?? "—") },
          { key: "presenterId", label: "Presenter ID", value: (row) => String(row.normalized_values.external_id ?? "—") },
          { key: "role", label: "Role / order", value: (row) => `${String(row.normalized_values.presenter_role ?? "—")} / ${String(row.normalized_values.presenter_order ?? "—")}` },
          { key: "room", label: "Room", value: (row) => String(row.normalized_values.location_name ?? "—") },
          { key: "state", label: "Validation", value: (row) => row.validation_state,
            render: (row) => <StatusBadge value={row.validation_state} /> },
          { key: "match", label: "Identity", value: (row) => ({ exact: "Existing person", no_match: "New person", ambiguous: "Ambiguous", conflict: "Conflict", strong_candidate: "Review candidate" }[row.match_outcome ?? ""] ?? row.match_outcome ?? "—") },
          { key: "issues", label: "Warnings / errors", value: (row) => row.issues.map((issue) => issue.message).join("; ") || "—" },
        ]}
        rowKey={(row) => row.import_row_id}
        label="Staged source rows"
        actions={(row) => row.conflict_state ? (
          <div className="button-row">
            {row.candidate_person_ids?.map((personId) => (
              <button className="button button--small" key={personId}
                onClick={() => void onResolve(row, "choose_person", personId)}>
                Match {personId.slice(0, 8)}…
              </button>
            ))}
            <button className="button button--small" onClick={() => void onResolve(row, "create_person")}>Create new</button>
            <button className="button button--small" onClick={() => void onResolve(row, "reject")}>Reject row</button>
          </div>
        ) : null}
      />
      <div className="button-row">
        <button className="button button--primary" disabled={busy || batch.status === "committed" || batch.conflict_count > 0}
          onClick={() => void onCommit()}>
          {batch.status === "committed" ? "Import committed" : busy ? "Committing…" : "Commit import"}
        </button>
        <StatusBadge value={batch.status} />
      </div>
    </div>
  );
}
