import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../../api/client";
import type {
  RoomDetail,
  RoomEndpoint,
  RoomPresentation,
  Row,
  SiteDeployment,
  SiteDevice,
  SiteMedia,
  SiteRoom,
  StorageTarget,
} from "../../api/types";
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
    const [health, registration, deployments, operations] = await Promise.all([
      siteApi.health(signal),
      siteApi.registration(signal),
      siteApi.deployments(signal),
      siteApi.operations(signal),
    ]);
    return { health, registration, deployments, operations };
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
                label="Rooms requiring attention"
                value={data.operations.attention.length}
                detail={`${data.operations.rooms.length} configured rooms`}
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
            <Panel
              title="Operator attention"
              description="Persisted room, media, processing, and transfer conditions that need action now."
            >
              {data.operations.attention.length ? (
                <DataTable
                  rows={data.operations.attention}
                  columns={[
                    {
                      key: "severity",
                      label: "Severity",
                      value: (row) => row.severity,
                      render: (row) => <StatusBadge value={row.severity} />,
                    },
                    {
                      key: "room",
                      label: "Room",
                      value: (row) => row.room_label || "Site-wide",
                    },
                    { key: "condition", label: "Condition", value: (row) => row.message },
                  ]}
                  rowKey={(row) => `${row.kind}-${row.room_id || "site"}`}
                  label="operational attention"
                  actions={(row) =>
                    row.room_id ? (
                      <Link className="button button--small" to={`/admin/rooms/${row.room_id}`}>
                        Open room
                      </Link>
                    ) : null
                  }
                />
              ) : (
                <Empty title="No room problems detected" />
              )}
            </Panel>
            <Panel title="Upcoming room sessions">
              {data.operations.upcoming_sessions.length ? (
                <DataTable
                  rows={data.operations.upcoming_sessions}
                  columns={[
                    { key: "room", label: "Room", value: (row) => row.room_label },
                    { key: "session", label: "Session", value: (row) => row.title },
                    {
                      key: "start",
                      label: "Starts",
                      value: (row) => row.starts_at,
                      render: (row) => when(row.starts_at),
                    },
                  ]}
                  rowKey={(row) => row.session_id}
                  label="upcoming room sessions"
                />
              ) : (
                <Empty title="No upcoming mapped sessions" />
              )}
            </Panel>
          </>
        )}
      </PageState>
    </Page>
  );
}

export function SiteCentralConnection() {
  const registration = useApi((signal) => siteApi.registration(signal), []);
  const [centralUrl, setCentralUrl] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (registration.data?.central_url) setCentralUrl(registration.data.central_url);
  }, [registration.data?.central_url]);

  async function testConnection() {
    setBusy(true); setError(undefined); setMessage("");
    try {
      const result = await siteApi.testCentral(centralUrl);
      setMessage(`Connected to ${result.central_identity} · ${result.status || "ready"}`);
    } catch (value) { setError(value instanceof Error ? value : new Error("Connection failed")); }
    finally { setBusy(false); }
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(undefined); setMessage("");
    try {
      await siteApi.configureCentral(centralUrl);
      setMessage("Central URL saved. Existing enrollment credentials were retained.");
      registration.refresh();
    } catch (value) { setError(value instanceof Error ? value : new Error("Save failed")); }
    finally { setBusy(false); }
  }

  async function reenroll() {
    if (!window.confirm("Clear the current machine credential and request enrollment from this Central?")) return;
    setBusy(true); setError(undefined); setMessage("");
    try {
      await siteApi.reenrollCentral();
      setMessage("Re-enrollment requested. Approve this Site in Central.");
      registration.refresh();
    } catch (value) { setError(value instanceof Error ? value : new Error("Re-enrollment failed")); }
    finally { setBusy(false); }
  }

  return <Page eyebrow="Site Admin" title="Central Connection" description="This Site initiates authenticated outbound synchronization and media transfers to Central.">
    <PageState {...registration} onRetry={registration.refresh}>{(data) => <>
      <div className="metrics">
        <Metric label="Enrollment" value={<StatusBadge value={data.registration_state} />} detail={data.credential_present ? "Machine credential present" : "No machine credential"} />
        <Metric label="Connection" value={<StatusBadge value={data.connection_status} />} detail={`Last sync ${when(data.last_successful_sync)}`} />
        <Metric label="Central identity" value={message.includes("Connected to") ? message.split(" · ")[0].replace("Connected to ", "") : "UPM Central"} detail={data.protocol_compatible ? "Protocol compatible" : "Identity verified when testing"} />
      </div>
      <Panel title="Central endpoint" description="Use a LAN URL such as http://192.168.100.127:8080 or an Internet HTTPS URL. No inbound Site port is required.">
        <form onSubmit={save} className="form-grid">
          <label>Central Server URL<input type="url" required value={centralUrl} onChange={(event) => setCentralUrl(event.target.value)} placeholder="https://central.example.com" /></label>
          <div className="button-row"><button className="button" type="button" disabled={busy || !centralUrl} onClick={testConnection}>Test Connection</button><button className="button button--primary" type="submit" disabled={busy || !centralUrl}>Save Central URL</button></div>
        </form>
        {message && <p role="status">{message}</p>}{error && <ErrorSurface error={error} />}
      </Panel>
      <Panel title="Connection history">
        <dl><dt>Configured endpoint</dt><dd>{data.central_url || "Not configured"}</dd><dt>Last connection</dt><dd>{when(data.last_connection_at || undefined)}</dd><dt>Last successful sync</dt><dd>{when(data.last_successful_sync)}</dd><dt>Last error</dt><dd>{data.last_error || "None"}</dd></dl>
      </Panel>
      <Panel title="Enrollment safety" description="Changing the URL retains the current machine credential. Re-enroll only when connecting to a different Central or when Central rejects the existing identity.">
        <button className="button button--danger" type="button" disabled={busy} onClick={reenroll}>Reconnect / Re-enroll</button>
      </Panel>
    </>}</PageState>
  </Page>;
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
              label: "Room",
              value: (row) => {
                const assigned = row.assigned_room as Row | undefined;
                return assigned?.label || row.location_name || "Unassigned";
              },
            },
            {
              key: "room-status",
              label: "Room mapping",
              value: (row) => row.room_mapping_status,
              render: (row) => <StatusBadge value={row.room_mapping_status} />,
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

const roomColumns: Column<SiteRoom>[] = [
  { key: "label", label: "Physical room", value: (row) => row.label },
  {
    key: "origin",
    label: "Origin",
    value: (row) => row.event_id ? "Event deployment" : "Reusable Site room",
  },
  {
    key: "health",
    label: "Health",
    value: (row) => row.summary.health,
    render: (row) => <StatusBadge value={row.summary.health} />,
  },
  {
    key: "primary",
    label: "Primary",
    value: (row) => row.endpoints.primary?.status || "unassigned",
    render: (row) => <StatusBadge value={row.endpoints.primary?.status || "unassigned"} />,
  },
  {
    key: "backup",
    label: "Backup",
    value: (row) => row.endpoints.backup?.status || "unassigned",
    render: (row) => <StatusBadge value={row.endpoints.backup?.status || "unassigned"} />,
  },
  {
    key: "presentations",
    label: "Presentations",
    value: (row) => row.summary.presentation_count,
    numeric: true,
  },
  {
    key: "ready",
    label: "Ready",
    value: (row) => row.summary.ready_count,
    numeric: true,
  },
  {
    key: "missing",
    label: "Missing",
    value: (row) => row.summary.missing_count,
    numeric: true,
  },
  {
    key: "errors",
    label: "Errors",
    value: (row) => row.summary.error_count,
    numeric: true,
  },
  {
    key: "next",
    label: "Next session",
    value: (row) => row.summary.next_session?.starts_at,
    render: (row) => when(row.summary.next_session?.starts_at),
  },
];

export function SiteRooms() {
  const rooms = useApi((signal) => siteApi.rooms(signal), []);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await siteApi.createRoom(label);
      setLabel("");
      rooms.refresh();
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Page eyebrow="Site operations" title="Rooms"
      description="Room-centered program, media, endpoint, and readiness state from this Site.">
      <Panel title="Create a Site room" description="Deployment automatically creates missing Event rooms; use this form for reusable Site infrastructure.">
        <form className="inline-form" onSubmit={submit}>
          <label className="field">Room label<input className="input" required value={label}
            onChange={(event) => setLabel(event.target.value)} /></label>
          <button className="button button--primary" disabled={saving || !label.trim()}>
            {saving ? "Creating…" : "Create room"}
          </button>
        </form>
        {error != null ? <ErrorSurface error={error} /> : null}
      </Panel>
      {rooms.loading ? <Loading /> : rooms.error ? (
        <ErrorSurface error={rooms.error} onRetry={rooms.refresh} />
      ) : rooms.data?.length ? (
        <DataTable rows={rooms.data} columns={roomColumns} rowKey={(row) => row.room_id}
          label="Site rooms" actions={(room) => (
            <Link className="button button--small" to={`/admin/rooms/${room.room_id}`}>
              Open
            </Link>
          )} />
      ) : <Empty title="No physical rooms configured" />}
    </Page>
  );
}

export function SiteRoomDetail() {
  const { roomId = "" } = useParams();
  const room = useApi((signal) => siteApi.room(roomId, signal), [roomId]);
  const deployments = useApi((signal) => siteApi.deployments(signal), []);
  const devices = useApi((signal) => siteApi.devices(signal), []);
  const [eventId, setEventId] = useState("");
  const selectedEvent = eventId || deployments.data?.[0]?.central_event_id || "";
  const locations = useApi(
    (signal) =>
      selectedEvent
        ? siteApi.programLocations(selectedEvent, signal)
        : Promise.resolve([]),
    [selectedEvent],
  );
  const [locationLabel, setLocationLabel] = useState("");
  const [mapping, setMapping] = useState(false);
  const [mappingError, setMappingError] = useState<unknown>();
  useEffect(() => {
    if (
      locations.data?.length &&
      !locations.data.some((item) => item.imported_label === locationLabel)
    ) {
      setLocationLabel(locations.data[0].imported_label);
    }
  }, [locations.data, locationLabel]);
  const saveMapping = async (targetRoomId: string | null) => {
    if (!selectedEvent || !locationLabel) return;
    setMapping(true);
    setMappingError(undefined);
    try {
      await siteApi.mapProgramLocation(selectedEvent, locationLabel, targetRoomId);
      room.refresh();
      locations.refresh();
    } catch (caught) {
      setMappingError(caught);
    } finally {
      setMapping(false);
    }
  };
  return (
    <PageState {...room} onRetry={room.refresh}>
      {(detail) => (
        <Page
          eyebrow="Room operations"
          title={detail.label}
          description={`Stable room UUID ${detail.room_id}`}
          actions={
            <Link className="button" to="/admin/rooms">
              Back to rooms
            </Link>
          }
        >
          <RoomMetrics room={detail} />
          <RoomEditor room={detail} onSaved={room.refresh} />
          <div className="panel-grid panel-grid--two">
            <Panel
              title="Program room mapping"
              description="Imported location labels remain labels until this Site maps them to the physical room UUID."
            >
              {!deployments.data?.length ? (
                <Empty title="No deployed program" />
              ) : (
                <div className="stack">
                  <label className="field">
                    Deployed event
                    <select
                      className="input"
                      value={selectedEvent}
                      onChange={(event) => {
                        setEventId(event.target.value);
                        setLocationLabel("");
                      }}
                    >
                      {deployments.data.map((item) => (
                        <option key={item.deployment_id} value={item.central_event_id}>
                          {item.event_name || item.central_event_id}
                        </option>
                      ))}
                    </select>
                  </label>
                  {locations.loading ? (
                    <Loading />
                  ) : locations.error ? (
                    <ErrorSurface error={locations.error} onRetry={locations.refresh} />
                  ) : locations.data?.length ? (
                    <>
                      <label className="field">
                        Imported program location
                        <select
                          className="input"
                          value={locationLabel}
                          onChange={(event) => setLocationLabel(event.target.value)}
                        >
                          {locations.data.map((item) => (
                            <option
                              key={item.normalized_imported_label}
                              value={item.imported_label}
                            >
                              {item.imported_label} — {item.mapping_status}
                              {item.room ? ` to ${item.room.label}` : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                      <div className="button-row">
                        <button
                          className="button button--primary"
                          disabled={mapping || !locationLabel || detail.archived || !detail.enabled}
                          onClick={() => saveMapping(detail.room_id)}
                        >
                          {mapping ? "Saving…" : `Map to ${detail.label}`}
                        </button>
                        <button
                          className="button"
                          disabled={mapping || !locationLabel}
                          onClick={() => saveMapping(null)}
                        >
                          Mark unmapped
                        </button>
                      </div>
                    </>
                  ) : (
                    <Empty title="No imported room locations in this program" />
                  )}
                  {mappingError != null ? <ErrorSurface error={mappingError} /> : null}
                </div>
              )}
            </Panel>
            <Panel
              title="Presentation Agents"
              description="Assignments are Site-server authoritative. Endpoint telemetry is shown only when reported."
            >
              <div className="stack">
                <EndpointAssignment
                  room={detail}
                  role="primary"
                  devices={devices.data || []}
                  onSaved={() => {
                    room.refresh();
                    devices.refresh();
                  }}
                />
                <EndpointAssignment
                  room={detail}
                  role="backup"
                  devices={devices.data || []}
                  onSaved={() => {
                    room.refresh();
                    devices.refresh();
                  }}
                />
                {devices.error ? <ErrorSurface error={devices.error} onRetry={devices.refresh} /> : null}
                {!devices.loading && !devices.error && !devices.data?.length ? (
                  <p className="muted">
                    No enrolled Agent endpoints exist yet. Agent enrollment and heartbeat reporting
                    remain outside this milestone.
                  </p>
                ) : null}
              </div>
            </Panel>
          </div>
          <Panel
            title="Room schedule"
            description="Sessions and presentations are read from the retained Site-local deployment projection."
          >
            {detail.sessions.length ? (
              <div className="schedule-list">
                {detail.sessions.map((session) => (
                  <section className="schedule-session" key={session.session_id}>
                    <header>
                      <div>
                        <span className="eyebrow">
                          {when(session.starts_at)} — {when(session.ends_at)}
                        </span>
                        <h4>{session.title}</h4>
                        <p className="muted">
                          {session.presenters.length
                            ? session.presenters.map((item) => item.name).join(", ")
                            : "No presenters supplied"}
                        </p>
                      </div>
                      <StatusBadge value={session.status} />
                    </header>
                    {session.presentations.length ? (
                      <DataTable
                        rows={session.presentations}
                        columns={presentationColumns}
                        rowKey={(item) => item.presentation_id}
                        label={`${session.title} presentations`}
                      />
                    ) : (
                      <Empty title="No presentations associated with this session" />
                    )}
                  </section>
                ))}
              </div>
            ) : (
              <Empty title="No sessions mapped to this room">
                Map a deployed program location to this room to build its operational schedule.
              </Empty>
            )}
          </Panel>
        </Page>
      )}
    </PageState>
  );
}

function RoomMetrics({ room }: { room: RoomDetail }) {
  return (
    <div className="metrics">
      <Metric label="Room health" value={<StatusBadge value={room.summary.health} />} />
      <Metric
        label="Presentations"
        value={room.summary.presentation_count}
        detail={`${room.summary.ready_count} ready`}
      />
      <Metric
        label="Problems"
        value={room.summary.missing_count + room.summary.error_count}
        detail={`${room.summary.missing_count} missing · ${room.summary.error_count} errors`}
      />
      <Metric
        label="Next session"
        value={room.summary.next_session ? when(room.summary.next_session.starts_at) : "None"}
        detail={room.summary.next_session?.title}
      />
    </div>
  );
}

function RoomEditor({ room, onSaved }: { room: RoomDetail; onSaved: () => void }) {
  const [label, setLabel] = useState(room.label);
  const [enabled, setEnabled] = useState(room.enabled);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    setLabel(room.label);
    setEnabled(room.enabled);
  }, [room.label, room.enabled]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      await siteApi.updateRoom(room.room_id, { label, enabled, revision: room.revision });
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(false);
    }
  };
  const archive = async () => {
    setSaving(true);
    setError(undefined);
    try {
      await siteApi.updateRoom(room.room_id, {
        archived: !room.archived,
        revision: room.revision,
      });
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(false);
    }
  };
  return (
    <Panel title="Room identity and lifecycle">
      <form className="inline-form" onSubmit={save}>
        <label className="field">
          Room label
          <input
            className="input"
            required
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <label className="check-field">
          <input
            type="checkbox"
            checked={enabled}
            disabled={room.archived}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          Enabled for operations
        </label>
        <button className="button button--primary" disabled={saving || !label.trim()}>
          Save room
        </button>
        <button className="button" type="button" disabled={saving} onClick={archive}>
          {room.archived ? "Restore room" : "Archive room"}
        </button>
      </form>
      {error != null ? <ErrorSurface error={error} /> : null}
    </Panel>
  );
}

function EndpointAssignment({
  room,
  role,
  devices,
  onSaved,
}: {
  room: RoomDetail;
  role: "primary" | "backup";
  devices: SiteDevice[];
  onSaved: () => void;
}) {
  const current = room.endpoints[role];
  const candidates = devices.filter(
    (device) => device.assignable || device.device_id === current?.device_id,
  );
  const [deviceId, setDeviceId] = useState(current?.device_id || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => setDeviceId(current?.device_id || ""), [current?.device_id]);
  const save = async (value: string | null) => {
    setSaving(true);
    setError(undefined);
    try {
      await siteApi.assignDevice(room.room_id, role, value);
      onSaved();
    } catch (caught) {
      setError(caught);
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="endpoint-card">
      <header>
        <strong>{role === "primary" ? "Primary Presentation Agent" : "Backup Presentation Agent"}</strong>
        <StatusBadge value={current?.status || "unassigned"} />
      </header>
      {current ? <EndpointFacts endpoint={current} /> : null}
      <div className="inline-form">
        <label className="field">
          Endpoint
          <select
            className="input"
            value={deviceId}
            onChange={(event) => setDeviceId(event.target.value)}
          >
            <option value="">Select an enrolled endpoint</option>
            {candidates.map((device) => (
              <option key={device.device_id} value={device.device_id}>
                {device.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="button button--primary"
          disabled={saving || !deviceId || room.archived || !room.enabled}
          onClick={() => save(deviceId)}
        >
          Assign
        </button>
        {current ? (
          <button className="button" disabled={saving} onClick={() => save(null)}>
            Clear
          </button>
        ) : null}
      </div>
      {error != null ? <ErrorSurface error={error} /> : null}
    </section>
  );
}

function EndpointFacts({ endpoint }: { endpoint: RoomEndpoint }) {
  return (
    <dl className="fact-grid">
      <div><dt>Name</dt><dd>{endpoint.name}</dd></div>
      <div><dt>UUID</dt><dd><code>{endpoint.device_id}</code></dd></div>
      <div><dt>Last heartbeat</dt><dd>{endpoint.telemetry_available ? when(endpoint.last_heartbeat) : "Not reported"}</dd></div>
      <div><dt>Network</dt><dd>{endpoint.ip_address || "Not reported"}</dd></div>
      <div><dt>Interface</dt><dd>{endpoint.interface || "Not reported"}</dd></div>
      <div><dt>Version</dt><dd>{endpoint.version || "Not reported"}</dd></div>
    </dl>
  );
}

const presentationColumns: Column<RoomPresentation>[] = [
  { key: "title", label: "Presentation", value: (row) => row.title },
  {
    key: "scheduled",
    label: "Scheduled",
    value: (row) => row.scheduled_at,
    render: (row) => when(row.scheduled_at),
  },
  {
    key: "state",
    label: "Operational media state",
    value: (row) => row.operational_status,
    render: (row) => <StatusBadge value={row.operational_status} />,
  },
  {
    key: "file",
    label: "Current file",
    value: (row) => row.media[0]?.filename || "No linked media",
  },
  {
    key: "version",
    label: "Version",
    value: (row) => row.media[0]?.version_number,
    numeric: true,
  },
  {
    key: "size",
    label: "Size",
    value: (row) => row.media[0]?.size_bytes,
    render: (row) => bytes(row.media[0]?.size_bytes),
    numeric: true,
  },
  {
    key: "processing",
    label: "Processing",
    value: (row) => row.media[0]?.processing_state || row.processing_status,
    render: (row) => (
      <StatusBadge value={row.media[0]?.processing_state || row.processing_status} />
    ),
  },
];

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
const mediaColumns: Column<SiteMedia>[] = [
  { key: "file", label: "File", value: (row) => row.file },
  {
    key: "presentation",
    label: "Linked presentation",
    value: (row) => row.presentation?.title || "Open file",
  },
  {
    key: "version",
    label: "Version",
    value: (row) => row.version_number,
    numeric: true,
  },
  { key: "type", label: "Type", value: (row) => row.mime_type || row.category },
  {
    key: "size",
    label: "Size",
    value: (row) => row.size_bytes,
    render: (row) => bytes(row.size_bytes),
    numeric: true,
  },
  {
    key: "state",
    label: "Authoritative state",
    value: (row) => row.availability,
    render: (row) => <StatusBadge value={row.availability} />,
  },
  {
    key: "processing",
    label: "Processing",
    value: (row) => row.processing_state,
    render: (row) => <StatusBadge value={row.processing_state || "not_started"} />,
  },
  {
    key: "ingested",
    label: "Ingested",
    value: (row) => row.ingested_at,
    render: (row) => when(row.ingested_at),
  },
  { key: "checksum", label: "SHA-256", value: (row) => row.checksum },
];
export function SiteStorage() {
  const result = useApi(async (signal) => {
    const [targets, media] = await Promise.all([
      siteApi.storage(signal).then((overview) => overview.roots),
      siteApi.media(signal),
    ]);
    return { targets, media };
  }, []);
  return (
    <Page
      eyebrow="Media ingestion"
      title="Storage"
      description="Managed Site media and capacity from authoritative storage records."
    >
      <PageState
        {...result}
        onRetry={result.refresh}
      >
        {(data) => (
          <div className="panel-grid">
            <Panel title="Storage targets">
              {data.targets.length ? (
                <DataTable
                  rows={data.targets}
                  columns={storageColumns}
                  rowKey={(row) => row.storage_target_id}
                  label="Storage targets"
                />
              ) : (
                <Empty title="No storage targets configured" />
              )}
            </Panel>
            <Panel
              title="Managed media"
              description="This is the UPM media catalog, not an unrestricted host filesystem browser."
            >
              {data.media.length ? (
                <DataTable
                  rows={data.media}
                  columns={mediaColumns}
                  rowKey={(row) => row.media_object_id}
                  label="managed media"
                />
              ) : (
                <Empty title="No managed media ingested" />
              )}
            </Panel>
          </div>
        )}
      </PageState>
    </Page>
  );
}
