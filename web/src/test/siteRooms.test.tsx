import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { PreferencesProvider } from "../state/preferences";
import { SessionProvider } from "../state/session";

const roomId = "01900000-0000-7000-8000-000000000101";
const eventId = "01900000-0000-7000-8000-000000000201";

test("renders the room-centered Site operational workflow", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = new URL(String(input), "http://test").pathname;
    let data: unknown;
    if (path.includes("central-registration")) {
      data = {
        site_id: "01900000-0000-7000-8000-000000000001",
        display_name: "Ballroom Site",
        registration_state: "active",
        connection_status: "never_connected",
        pending_outbound: 0,
        failed_sync: 0,
        protocol_compatible: true,
      };
    } else if (path === `/api/v1/rooms/${roomId}`) {
      data = {
        room_id: roomId,
        site_id: "01900000-0000-7000-8000-000000000001",
        label: "Room 101",
        enabled: true,
        archived: false,
        revision: 1,
        program_mappings: [],
        endpoints: {},
        summary: {
          health: "warning",
          session_count: 1,
          presentation_count: 1,
          ready_count: 0,
          missing_count: 1,
          error_count: 0,
          processing_count: 0,
          transfer_pending_count: 0,
          next_session: {
            session_id: "session-1",
            title: "Opening Session",
            starts_at: "2026-08-11T13:00:00Z",
          },
        },
        sessions: [
          {
            session_id: "session-1",
            event_id: eventId,
            title: "Opening Session",
            starts_at: "2026-08-11T13:00:00Z",
            ends_at: "2026-08-11T14:00:00Z",
            status: "scheduled",
            presenters: [{ name: "Presenter One", role: "presenter" }],
            presentations: [
              {
                presentation_id: "presentation-1",
                title: "Opening deck",
                workflow_status: "expected",
                processing_status: "not_started",
                operational_status: "missing",
                media: [],
              },
            ],
          },
        ],
      };
    } else if (path === "/api/v1/event-deployments") {
      data = [
        {
          deployment_id: "deployment-1",
          central_event_id: eventId,
          event_name: "UPM Expo",
          status: "deployed",
          desired_revision: 1,
          applied_revision: 1,
          central_connected: false,
        },
      ];
    } else if (path.includes("program-room-locations")) {
      data = [
        {
          event_id: eventId,
          imported_label: "Room 101",
          normalized_imported_label: "room 101",
          mapping_status: "unmapped",
          session_count: 1,
        },
      ];
    } else if (path === "/api/v1/devices") {
      data = [];
    } else {
      data = [];
    }
    return Response.json(data);
  });
  render(
    <MemoryRouter initialEntries={[`/admin/rooms/${roomId}`]}>
      <PreferencesProvider>
        <SessionProvider>
          <App deployment="site" />
        </SessionProvider>
      </PreferencesProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByRole("heading", { name: "Room 101" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Opening Session" })).toBeInTheDocument();
  expect(screen.getByText("Opening deck")).toBeInTheDocument();
  expect(screen.getByText("Missing")).toBeInTheDocument();
  expect(
    await screen.findByRole("button", { name: "Map to Room 101" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Primary Presentation Agent")).toBeInTheDocument();
  expect(screen.getByText(/Agent enrollment and heartbeat reporting remain outside/)).toBeInTheDocument();
});
