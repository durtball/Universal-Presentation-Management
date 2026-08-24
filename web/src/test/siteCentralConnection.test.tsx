import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";
import { SiteCentralConnection } from "../pages/site/SitePages";

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/api/v1/central-registration/test")) {
      return Response.json({ reachable: true, central_url: "https://central.example.com", central_identity: "upm-central", status: "foundation-ready" });
    }
    if (path.includes("/api/v1/central-registration/endpoint") && init?.method === "PUT") {
      return Response.json({ site_id: "site-1", central_url: "https://central.example.com" });
    }
    return Response.json({ site_id: "site-1", display_name: "Venue", central_url: "http://192.168.100.127:8080", registration_state: "active", connection_status: "connected", credential_present: true, protocol_compatible: true, pending_outbound: 0, failed_sync: 0, last_successful_sync: "2026-08-24T12:00:00Z", last_error: null });
  }));
});

test("tests and saves the canonical Central endpoint without re-enrolling", async () => {
  render(<MemoryRouter><SiteCentralConnection /></MemoryRouter>);
  const input = await screen.findByLabelText("Central Server URL");
  fireEvent.change(input, { target: { value: "https://central.example.com" } });
  fireEvent.click(screen.getByRole("button", { name: "Test Connection" }));
  expect(await screen.findByText(/Connected to upm-central/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save Central URL" }));
  expect(await screen.findByText(/Existing enrollment credentials were retained/)).toBeInTheDocument();
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url, options]) => String(url).includes("/endpoint") && options?.method === "PUT")).toBe(true));
  expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/request"))).toBe(false);
});
