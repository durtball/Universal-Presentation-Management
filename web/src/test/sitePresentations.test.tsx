import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { siteApi } from "../api/site";
import { SitePresentations } from "../pages/SitePresentations";

describe("Site presentations operations", () => {
  it("renders current confirmed media, all associated sessions, details, and download", async () => {
    vi.spyOn(siteApi, "deployments").mockResolvedValue([{ central_event_id: "event", event_name: "Event" } as never]);
    vi.spyOn(siteApi, "presentationOperations").mockResolvedValue({ total: 1, limit: 50, offset: 0, items: [{
      presentation_id: "presentation", presentation_identifier: "3542488", title: "Clinical Update", presenters: ["Sara Sadeghi"],
      sessions: [{ session_id: "s1", session_code: "S-1", title: "Morning", starts_at: "2026-08-18T09:00:00Z", room: "Ballroom A" }, { session_id: "s2", session_code: "S-2", title: "Encore", starts_at: "2026-08-18T14:00:00Z", room: "Ballroom B" }],
      filename: "3542488-Sadeghi.pptx", size_bytes: 2048, current_version: 2, readiness: "available", delivery_state: "not_delivered", source: "presentation_version", received_at: "2026-08-18T08:00:00Z", confirmed_at: "2026-08-18T08:01:00Z", confirmed_by: "site-operator", mime_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation", sha256: "a".repeat(64), version_history: [{ presentation_version_id: "v2", version_number: 2, filename: "3542488-Sadeghi.pptx", size_bytes: 2048, received_at: "2026-08-18T08:00:00Z" }, { presentation_version_id: "v1", version_number: 1, filename: "draft.pptx", size_bytes: 1024, received_at: "2026-08-17T08:00:00Z" }], download_url: "/api/v1/presentation-versions/version/download",
    }] });
    render(<SitePresentations />);
    expect(await screen.findByText("3542488-Sadeghi.pptx")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("Morning")).toBeInTheDocument();
    expect(screen.getByText("Encore")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download current" })).toHaveAttribute("href", "/api/v1/presentation-versions/version/download");
    expect(screen.getByRole("button", { name: "Open unavailable" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "View Details" }));
    expect(screen.getByText(/site-operator/)).toBeInTheDocument();
    expect(screen.getByText(/v2 — Current/)).toBeInTheDocument();
    expect(screen.getByText(/draft.pptx/)).toBeInTheDocument();
    expect(screen.getByText("S-1", { exact: false })).toBeInTheDocument();
  });
});
