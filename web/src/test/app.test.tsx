import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { PreferencesProvider } from "../state/preferences";
import { SessionProvider } from "../state/session";

function renderApp(deployment: "central" | "site", path = "/admin") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PreferencesProvider>
        <SessionProvider>
          <App deployment={deployment} />
        </SessionProvider>
      </PreferencesProvider>
    </MemoryRouter>,
  );
}
test("redirects an unauthenticated Central administrator to login", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path === "/health") return Response.json({ service: "upm-central", status: "foundation-ready" });
    return Response.json({ detail: "not authenticated" }, { status: 401 });
  });
  renderApp("central");
  expect(await screen.findByRole("heading", { name: "Administrator login" })).toBeInTheDocument();
});
test("protects direct navigation to a Central program page", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    Response.json({ detail: "not authenticated" }, { status: 401 }),
  );
  renderApp("central", "/admin/people");
  expect(await screen.findByRole("heading", { name: "Administrator login" })).toBeInTheDocument();
});
test("renders Site operational data and offline-local autonomy", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = new URL(String(input), "http://test").pathname;
    const data =
      path === "/health"
        ? { service: "upm-site", status: "foundation-ready" }
        : path.includes("central-registration")
          ? {
              site_id: "01900000-0000-7000-8000-000000000001",
              display_name: "Ballroom Site",
              registration_state: "active",
              connection_status: "never_connected",
              pending_outbound: 0,
              failed_sync: 0,
              protocol_compatible: true,
            }
          : path.includes("storage")
            ? []
            : path.includes("operations/dashboard")
              ? {
                  rooms: [],
                  attention: [],
                  upcoming_sessions: [],
                  failed_processing_jobs: 0,
                  failed_transfer_jobs: 0,
                }
            : [];
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  renderApp("site");
  await waitFor(() =>
    expect(screen.getByText("Site-local autonomy")).toBeInTheDocument(),
  );
  expect(screen.getAllByText("Ballroom Site").length).toBeGreaterThan(0);
  expect(
    screen.getByText(/Central connectivity is not required/i),
  ).toBeInTheDocument();
});
test("Site unavailable state is actionable", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("offline"));
  renderApp("site");
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("Service unavailable"),
  );
});
