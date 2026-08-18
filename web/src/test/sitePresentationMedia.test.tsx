import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { siteApi } from "../api/site";
import { SitePresentationMedia } from "../pages/SitePresentationMedia";

const candidate = {
  presentation_id: "11111111-1111-1111-1111-111111111111",
  presentation_identifier: "P-3542488",
  external_presentation_id: "3542488",
  title: "Clinical Update",
  presenters: ["Sara Sadeghi"],
  presenter_given_name: "Sara",
  presenter_family_name: "Sadeghi",
  session_id: "22222222-2222-2222-2222-222222222222",
  session_code: "3542488",
  session: "Sadeghi Clinical Update",
  room: "Ballroom A",
};

const intakeItem = {
  media_object_id: "33333333-3333-3333-3333-333333333333",
  filename: "3542488-Sadeghi.pptx",
  source: "open_file",
  size_bytes: 12,
  suggestion: candidate,
  confidence: "high",
  match_state: "suggested",
  match_reason: "Session ID 3542488 and presenter last name Sadeghi matched filename",
};

function mockSite() {
  vi.spyOn(siteApi, "deployments").mockResolvedValue([{
    central_event_id: "44444444-4444-4444-4444-444444444444",
    event_name: "Test Event",
  } as never]);
}

describe("Site presentation media canonical matching", () => {
  it("preselects a suggested canonical ID and confirms it only after operator action", async () => {
    mockSite();
    const intake = vi.spyOn(siteApi, "mediaIntake")
      .mockResolvedValueOnce({ items: [intakeItem], total: 1, limit: 50, offset: 0 })
      .mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    const confirm = vi.spyOn(siteApi, "confirmMedia").mockResolvedValue({});

    render(<SitePresentationMedia />);
    const selection = await screen.findByLabelText("Canonical match for 3542488-Sadeghi.pptx");
    expect(selection).toHaveValue(candidate.presentation_id);
    expect(confirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(confirm).toHaveBeenCalledWith(
      intakeItem.media_object_id, candidate.presentation_id,
    ));
    expect(intake).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("No media needs review")).toBeInTheDocument();
  });

  it("uses bounded server lookup for presenter and canonical identifier searches", async () => {
    mockSite();
    vi.spyOn(siteApi, "mediaIntake").mockResolvedValue({
      items: [{ ...intakeItem, suggestion: null, match_state: "needs_review" }],
      total: 1, limit: 50, offset: 0,
    });
    const lookup = vi.spyOn(siteApi, "presentationLookup").mockResolvedValue({ items: [candidate] });
    render(<SitePresentationMedia />);
    fireEvent.click(await screen.findByRole("button", { name: "Rescan All Unmatched" }));
    await waitFor(() => expect(siteApi.mediaIntake).toHaveBeenCalledTimes(2));
    fireEvent.click(await screen.findByRole("button", { name: "Find…" }));
    const search = screen.getByLabelText("Search Session / Presenter");
    fireEvent.change(search, { target: { value: "Sadeghi" } });
    await waitFor(() => expect(lookup).toHaveBeenCalledWith(
      expect.any(String), "Sadeghi", expect.any(AbortSignal),
    ));
    fireEvent.click(await screen.findByText(/P-3542488 — Clinical Update/));
    fireEvent.click(screen.getByRole("button", { name: "Find…" }));
    fireEvent.change(screen.getByLabelText("Search Session / Presenter"), {
      target: { value: "3542488" },
    });
    await waitFor(() => expect(lookup).toHaveBeenCalledWith(
      expect.any(String), "3542488", expect.any(AbortSignal),
    ));
  });
});
