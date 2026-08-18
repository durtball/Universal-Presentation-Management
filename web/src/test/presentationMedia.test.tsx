import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MediaUploadDialog, PresentationMediaDetail, ReplicationStatus } from "../components/presentationMedia";
import { goodMatchIds, MatchControl, selectedConfirmations } from "../pages/PresentationMedia";

describe("presentation media workflows", () => {
  it("queues multiple files and reports upload completion", async () => {
    const upload = vi.fn(async (_file: File, progress: (value: number) => void) => { progress(100); return { state: "staged" as const }; });
    render(<MediaUploadDialog title="Upload media" onClose={() => undefined} upload={upload} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["one"], "UPM-101.pptx"), new File(["two"], "unknown.pdf")] } });
    fireEvent.click(screen.getByRole("button", { name: "Upload 2 files" }));
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(await screen.findAllByText("Staged")).toHaveLength(3); // summary label plus two items
  });

  it("keeps 2,000 rows searchable without rendering the entire list", () => {
    const upload = vi.fn();
    render(<MediaUploadDialog title="Bulk Import" onClose={() => undefined} upload={upload} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const files = Array.from({ length: 2_000 }, (_, index) => new File([String(index)], `file-${index}.pptx`));
    fireEvent.change(input, { target: { files } });
    expect(screen.getByText("Bulk Import — 2000 files")).toBeInTheDocument();
    expect(screen.queryByText("file-1999.pptx")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Search batch files"), { target: { value: "file-1999" } });
    expect(screen.getByText("file-1999.pptx")).toBeInTheDocument();
    expect(document.querySelectorAll(".upload-queue article").length).toBeLessThan(30);
  });

  it("registers the complete batch and opens its correlated log", async () => {
    const upload = vi.fn(async () => ({ state: "staged" as const }));
    const registerBatch = vi.fn(async () => "batch-527");
    const viewLog = vi.fn();
    render(<MediaUploadDialog title="Bulk Import" onClose={() => undefined} upload={upload} registerBatch={registerBatch} onViewBatchLog={viewLog} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: Array.from({ length: 527 }, (_, index) => new File(["x"], `${index}.pptx`)) } });
    fireEvent.click(screen.getByRole("button", { name: "Upload 527 files" }));
    await waitFor(() => expect(registerBatch).toHaveBeenCalledWith(527, []));
    fireEvent.click(await screen.findByRole("button", { name: "View Batch Log" }));
    expect(viewLog).toHaveBeenCalledWith("batch-527");
  });

  it("shows current version, complete history, canonical names, and local readiness", () => {
    render(<PresentationMediaDetail onClose={() => undefined} row={{
      presentation_id: "p1", presentation_identifier: "UPM-101", title: "Opening",
      media_state: "available", versions: [
        { presentation_version_id: "v2", version_number: 2, media: { media_object_id: "m2", original_filename: "opening-final.pptx", canonical_filename: "UPM-101_v2.pptx", availability: "available" } },
        { presentation_version_id: "v1", version_number: 1, media: { media_object_id: "m1", original_filename: "opening.pptx", canonical_filename: "UPM-101_v1.pptx", availability: "available" } },
      ],
    }} />);
    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("Version 1")).toBeInTheDocument();
    expect(screen.getByText("UPM-101_v2.pptx")).toBeInTheDocument();
  });

  it("keeps local media separate from failed Central replication", () => {
    render(<ReplicationStatus replication={{ replication_session_id: "r1", state: "failed", confirmed_offset: 50, expected_size: 100, retry_count: 2, last_error: "Central connection refused" }} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/Local media remains ready/)).toBeInTheDocument();
    expect(screen.getAllByText(/50%/)).toHaveLength(2);
  });
});

describe("operator-confirmed media selection", () => {
  it("preselects, searches, and confirms the canonical Sadeghi candidate", async () => {
    const candidate = {
      presentation_id: "11111111-1111-1111-1111-111111111111",
      presentation_identifier: "3542488",
      external_presentation_id: "3542488",
      title: "Clinical Update",
      session_title: "3542488 Clinical Update",
      session_external_id: "3542488",
      room: "Ballroom A",
      presenters: [{ family_name: "Sadeghi", given_name: "Sara", display_name: "Sara Sadeghi" }],
    };
    const item = {
      event_id: "22222222-2222-2222-2222-222222222222",
      media_import_id: "33333333-3333-3333-3333-333333333333",
      original_filename: "3542488-Sadeghi.pptx",
      match_state: "suggested",
      suggested_candidate: candidate,
      match_candidates: [{ presentation_id: candidate.presentation_id, score: 200, confidence: "high" as const, evidence: ["Presentation ID 3542488 matched filename", "Presenter last name Sadeghi matched filename"] }],
      import_state: "needs_review", sync_state: "local", origin: "central", retry_count: 0,
      created_at: "2026-01-01", updated_at: "2026-01-01",
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ candidates: [candidate] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const done = vi.fn();
    render(<MatchControl item={item} candidates={[]} eventId={item.event_id} onDone={done} csrf="csrf" />);
    expect(screen.getByLabelText(`Match ${item.original_filename}`)).toHaveValue(candidate.presentation_id);
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Search Session / Presenter"), { target: { value: "Sadeghi" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.objectContaining({ pathname: expect.stringContaining("presentation-match-candidates") }), expect.anything()));
    expect(await screen.findByRole("option", { name: /3542488.*Sadeghi/ })).toBeInTheDocument();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(item), { status: 200, headers: { "Content-Type": "application/json" } }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain(`/assignment/${candidate.presentation_id}`);
  });

  it("selects only visible unambiguous high-confidence suggestions", () => {
    const base = { event_id: "e", original_filename: "deck.pptx", import_state: "needs_review", sync_state: "local", origin: "central", retry_count: 0, created_at: "2026-01-01", updated_at: "2026-01-01" };
    const good = { ...base, media_import_id: "good", match_state: "suggested", suggested_candidate: { presentation_id: "p1", presentation_identifier: "ID", title: "Title", presenters: [] }, match_candidates: [{ presentation_id: "p1", score: 155, confidence: "high" as const, evidence: ["ID matched"] }] };
    const ambiguous = { ...base, media_import_id: "ambiguous", match_state: "ambiguous", match_candidates: [{ presentation_id: "p2", score: 55, confidence: "medium" as const, evidence: [] }] };
    const tied = { ...base, media_import_id: "tied", match_state: "suggested", match_candidates: [{ presentation_id: "p3", score: 100, confidence: "high" as const, evidence: [] }, { presentation_id: "p4", score: 100, confidence: "high" as const, evidence: [] }] };
    expect(goodMatchIds([good, ambiguous, tied])).toEqual(["good"]);
    expect(goodMatchIds([good], "missing-name")).toEqual([]);
  });
  it("lets an operator replace a suggestion without confirming it", async () => {
    const suggested = { presentation_id: "p1", presentation_identifier: "3542488", title: "Clinical Update", presenters: [{ display_name: "Sara Sadeghi" }] };
    const replacement = { presentation_id: "p2", presentation_identifier: "999", title: "Replacement", presenters: [{ display_name: "Pat Lee" }] };
    const item = { event_id: "e", media_import_id: "m", original_filename: "3542488-Sadeghi.pptx", match_state: "suggested", suggested_candidate: suggested, match_candidates: [{ presentation_id: "p1", score: 200, confidence: "high" as const, evidence: [] }], import_state: "needs_review", sync_state: "local", origin: "central", retry_count: 0, created_at: "2026-01-01", updated_at: "2026-01-01" };
    const changed = vi.fn();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ candidates: [replacement] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    render(<MatchControl item={item} candidates={[]} eventId="e" onSelectionChange={changed} onDone={vi.fn()} csrf="csrf" />);
    fireEvent.change(screen.getByLabelText("Search Session / Presenter"), { target: { value: "Pat" } });
    const option = await screen.findByRole("option", { name: /999.*Pat Lee/ });
    fireEvent.change(screen.getByLabelText(`Match ${item.original_filename}`), { target: { value: option.getAttribute("value") } });
    expect(changed).toHaveBeenCalledWith("p2");
    expect(screen.getByLabelText(`Match ${item.original_filename}`)).toHaveValue("p2");
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
  });

  it("builds bulk confirmation from exactly the selected suggestions and overrides", () => {
    const base = { event_id: "e", original_filename: "deck.pptx", match_state: "suggested", import_state: "needs_review", sync_state: "local", origin: "central", retry_count: 0, created_at: "2026-01-01", updated_at: "2026-01-01" };
    const rows = [
      { ...base, media_import_id: "one", match_candidates: [{ presentation_id: "p1", score: 150, confidence: "high" as const, evidence: [] }] },
      { ...base, media_import_id: "two", match_candidates: [{ presentation_id: "p2", score: 150, confidence: "high" as const, evidence: [] }] },
      { ...base, media_import_id: "none", match_state: "unmatched", match_candidates: [] },
    ];
    expect(selectedConfirmations(rows, new Set(["one", "none"]), new Map([["one", "replacement"]]))).toEqual([{ media_import_id: "one", presentation_id: "replacement" }]);
  });

});
