import { useState } from "react";
import { ApiError } from "../../api/client";
import type { Row, SiteDeployment, StorageTarget } from "../../api/types";
import { siteApi } from "../../api/site";
import { DataTable, type Column } from "../../components/DataTable";
import {
  Empty,
  ErrorSurface,
  Loading,
  PageState,
} from "../../components/Feedback";
import { Metric, Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../hooks/useApi";
import { bytes, when } from "../central/Shared";

const deploymentColumns: Column<SiteDeployment>[] = [
  {
    key: "event",
    label: "Deployed event",
    value: (row) => row.event_name ?? row.central_event_id,
  },
  {
    key: "status",
    label: "Status",
    value: (row) => row.status,
    render: (row) => <StatusBadge value={row.status} />,
  },
  {
    key: "revision",
    label: "Revision",
    value: (row) => `${row.applied_revision} / ${row.desired_revision}`,
  },
  {
    key: "central",
    label: "Central link",
    value: (row) => (row.central_connected ? "online" : "offline"),
    render: (row) => (
      <StatusBadge value={row.central_connected ? "online" : "offline"} />
    ),
  },
];
export function SiteOverview() {
  const result = useApi(async (signal) => {
    const [health, registration, deployments, storage] = await Promise.all([
      siteApi.health(signal),
      siteApi.registration(signal),
      siteApi.deployments(signal),
      siteApi.storage(signal),
    ]);
    return { health, registration, deployments, storage };
  }, []);
  return (
    <Page
      eyebrow="UPM Site"
      title="Local operations"
      description="Site-authoritative status remains available without a live Central connection."
    >
      <PageState {...result} onRetry={result.refresh}>
        {(data) => (
          <>
            <div className="autonomy-banner">
              <strong>Site-local autonomy</strong>
              <span>
                Program projections, storage, and local operations use this Site
                only. Central connectivity is not required.
              </span>
            </div>
            <div className="metrics">
              <Metric
                label="Site API"
                value={
                  <StatusBadge
                    value={
                      data.health.status === "foundation-ready"
                        ? "healthy"
                        : data.health.status
                    }
                  />
                }
              />
              <Metric
                label="Central connectivity"
                value={
                  <StatusBadge value={data.registration.connection_status} />
                }
                detail={`Last sync ${when(data.registration.last_successful_sync)}`}
              />
              <Metric
                label="Synchronization"
                value={
                  <StatusBadge
                    value={
                      data.registration.failed_sync
                        ? "failed"
                        : data.registration.pending_outbound
                          ? "synchronizing"
                          : "synchronized"
                    }
                  />
                }
                detail={`${data.registration.pending_outbound} queued`}
              />
              <Metric
                label="Storage targets"
                value={data.storage.length}
                detail={`${data.storage.filter((item) => item.available).length} available`}
              />
            </div>
            {data.registration.last_error && (
              <ErrorSurface error={new Error(data.registration.last_error)} />
            )}
            <Panel title="Current deployed programs">
              <DataTable
                rows={data.deployments}
                columns={deploymentColumns}
                rowKey={(row) => row.deployment_id}
                label="Event deployments"
              />
            </Panel>
          </>
        )}
      </PageState>
    </Page>
  );
}

export function SiteProgram() {
  const deployments = useApi((signal) => siteApi.deployments(signal), []);
  const [eventId, setEventId] = useState("");
  const selected = eventId || deployments.data?.[0]?.central_event_id || "";
  const program = useApi(
    (signal) =>
      selected ? siteApi.program(selected, signal) : Promise.resolve(undefined),
    [selected],
  );
  return (
    <Page
      eyebrow="Site-local projection"
      title="Program"
      description="The last complete deployed snapshot remains usable through Central or WAN outages."
    >
      {deployments.loading ? (
        <Loading />
      ) : deployments.error ? (
        <ErrorSurface error={deployments.error} />
      ) : !deployments.data?.length ? (
        <Empty title="No deployed events">
          A complete event snapshot will appear after Central deploys it to this
          Site.
        </Empty>
      ) : (
        <>
          <label className="field field--inline">
            Deployed event
            <select
              className="input"
              value={selected}
              onChange={(event) => setEventId(event.target.value)}
            >
              {deployments.data.map((item) => (
                <option key={item.deployment_id} value={item.central_event_id}>
                  {item.event_name ?? item.central_event_id}
                </option>
              ))}
            </select>
          </label>
          {program.loading ? (
            <Loading />
          ) : program.error instanceof ApiError && program.error.status === 404 ? (
            <Empty title="Program rows not projected">
              Deployment metadata is available locally, but this retained snapshot has no program rows.
            </Empty>
          ) : program.error ? (
            <ErrorSurface error={program.error} onRetry={program.refresh} />
          ) : program.data ? (
            <ProgramTables program={program.data} />
          ) : null}
        </>
      )}
    </Page>
  );
}

function ProgramTables({ program }: { program: Row }) {
  const sessions = (program.sessions ?? []) as Row[];
  const presentations = (program.presentations ?? []) as Row[];
  const participants = (program.participants ?? []) as Row[];
  return (
    <div className="panel-grid">
      <Panel title={`${sessions.length} sessions`}>
        <DataTable
          rows={sessions}
          columns={[
            { key: "title", label: "Session", value: (row) => row.title },
            {
              key: "start",
              label: "Starts",
              value: (row) => row.starts_at,
              render: (row) => when(row.starts_at),
            },
            {
              key: "room",
              label: "Location",
              value: (row) => row.location_name,
            },
            {
              key: "status",
              label: "Status",
              value: (row) => row.status,
              render: (row) => <StatusBadge value={row.status} />,
            },
          ]}
          rowKey={(row) => String(row.session_id)}
          label="Site sessions"
        />
      </Panel>
      <Panel title={`${presentations.length} presentations`}>
        <DataTable
          rows={presentations}
          columns={[
            { key: "title", label: "Presentation", value: (row) => row.title },
            {
              key: "workflow",
              label: "Workflow",
              value: (row) => row.workflow_status,
              render: (row) => <StatusBadge value={row.workflow_status} />,
            },
            {
              key: "processing",
              label: "Processing",
              value: (row) => row.processing_status,
              render: (row) => <StatusBadge value={row.processing_status} />,
            },
          ]}
          rowKey={(row) => String(row.presentation_id)}
          label="Site presentations"
        />
      </Panel>
      <Panel title={`${participants.length} participants`}>
        <DataTable
          rows={participants}
          columns={[
            {
              key: "name",
              label: "Participant",
              value: (row) => row.display_name,
            },
            {
              key: "organization",
              label: "Organization",
              value: (row) => row.organization,
            },
            {
              key: "status",
              label: "Status",
              value: (row) => row.participant_status,
              render: (row) => <StatusBadge value={row.participant_status} />,
            },
          ]}
          rowKey={(row) => String(row.event_participation_id)}
          label="Site participants"
        />
      </Panel>
    </div>
  );
}

const storageColumns: Column<StorageTarget>[] = [
  { key: "name", label: "Target", value: (row) => row.display_name },
  {
    key: "health",
    label: "Health",
    value: (row) => row.health,
    render: (row) => <StatusBadge value={row.health} />,
  },
  {
    key: "available",
    label: "Available",
    value: (row) => (row.available ? "available" : "unavailable"),
    render: (row) => (
      <StatusBadge value={row.available ? "available" : "unavailable"} />
    ),
  },
  {
    key: "free",
    label: "Free",
    value: (row) => row.free_bytes,
    render: (row) => bytes(row.free_bytes),
    numeric: true,
  },
  {
    key: "total",
    label: "Total",
    value: (row) => row.total_bytes,
    render: (row) => bytes(row.total_bytes),
    numeric: true,
  },
  { key: "detail", label: "Detail", value: (row) => row.detail },
];
export function SiteStorage() {
  const result = useApi((signal) => siteApi.storage(signal), []);
  return (
    <Page
      eyebrow="Media ingestion"
      title="Storage"
      description="Capacity and write readiness for Site-authoritative media targets."
    >
      <PageState
        {...result}
        empty={(rows) => !rows.length}
        onRetry={result.refresh}
      >
        {(rows) => (
          <DataTable
            rows={rows}
            columns={storageColumns}
            rowKey={(row) => row.storage_target_id}
            label="Storage targets"
          />
        )}
      </PageState>
    </Page>
  );
}
