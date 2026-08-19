import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { EventScoped } from "../pages/central/EventScoped";
import { SessionProvider } from "../state/session";

const eventId = "01900000-0000-7000-8000-000000000010";

test("shows and searches by the canonical person name when the participation has no override", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path === "/api/v1/auth/session")
      return Response.json({
        authenticated: true,
        csrf_token: "csrf",
        expires_at: "2030-01-01T00:00:00Z",
        user: { user_id: "admin", username: "admin", display_name: "Admin", roles: ["administrator"] },
      });
    if (path === "/api/v1/admin/events")
      return Response.json([{ event_id: eventId, name: "Annual Summit", timezone: "UTC", deployments: [] }]);
    if (path === `/api/v1/admin/events/${eventId}/participants`)
      return Response.json([
        {
          event_participation_id: "01900000-0000-7000-8000-000000000011",
          person_id: "01900000-0000-7000-8000-000000000012",
          person_display_name: "Dr. Jane Doe",
          display_name: null,
          professional_title: "Chief Scientist",
          organization: "UPM Test",
          primary_email: "jane@example.com",
          is_presenter: true,
          sessions: [{ title: "Opening Session" }],
        },
      ]);
    throw new Error(`Unexpected GET ${path}`);
  });

  render(
    <MemoryRouter>
      <SessionProvider>
        <EventScoped type="presenters" />
      </SessionProvider>
    </MemoryRouter>,
  );

  expect(await screen.findByText("Dr. Jane Doe")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search Presenters"), { target: { value: "Jane" } });
  expect(screen.getByText("Dr. Jane Doe")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Search Presenters"), { target: { value: "missing" } });
  expect(screen.queryByText("Dr. Jane Doe")).not.toBeInTheDocument();
});
