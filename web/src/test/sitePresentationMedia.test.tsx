import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "confirmed" } });
    const confirmedRow = screen.getByText("3542488-Sadeghi.pptx").closest("tr")!;
    expect(within(confirmedRow).getByLabelText("Select 3542488-Sadeghi.pptx")).toBeDisabled();
    expect(within(confirmedRow).getByLabelText("Canonical match for 3542488-Sadeghi.pptx")).toBeDisabled();
    expect(within(confirmedRow).queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument();
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

  it("sorts Site queue columns without clearing valid selections", async () => {
    mockSite();
    const secondCandidate = { ...candidate, presentation_id: "55555555-5555-5555-5555-555555555555", presentation_identifier: "Z-2", title: "Zulu", presenters: ["Zulu Presenter"], session: "Zulu Session" };
    const second = { ...intakeItem, media_object_id: "66666666-6666-6666-6666-666666666666", filename: "zulu.pptx", size_bytes: 4096, received_at: "2026-02-01T00:00:00Z", suggestion: secondCandidate };
    const first = { ...intakeItem, filename: "alpha.pptx", size_bytes: 10, received_at: "2026-01-01T00:00:00Z" };
    vi.spyOn(siteApi, "mediaIntake").mockResolvedValue({ items: [second, first], total: 2, limit: 50, offset: 0 });
    render(<SitePresentationMedia />);
    const checkbox = await screen.findByLabelText("Select alpha.pptx");
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole("button", { name: "Sort by File Size" }));
    expect(document.querySelector("tbody tr strong")?.textContent).toBe("alpha.pptx");
    expect(checkbox).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Sort by File Size" }));
    expect(document.querySelector("tbody tr strong")?.textContent).toBe("zulu.pptx");
    expect(screen.getByLabelText("Select alpha.pptx")).toBeChecked();
  });

});
