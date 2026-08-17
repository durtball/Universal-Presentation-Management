import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MediaUploadDialog, PresentationMediaDetail, ReplicationStatus } from "../components/presentationMedia";
import { goodMatchIds } from "../pages/PresentationMedia";

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
  it("selects only visible unambiguous high-confidence suggestions", () => {
    const base = { event_id: "e", original_filename: "deck.pptx", import_state: "needs_review", sync_state: "local", origin: "central", retry_count: 0, created_at: "2026-01-01", updated_at: "2026-01-01" };
    const good = { ...base, media_import_id: "good", match_state: "suggested", match_candidates: [{ presentation_id: "p1", score: 155, confidence: "high" as const, evidence: ["ID matched"] }] };
    const ambiguous = { ...base, media_import_id: "ambiguous", match_state: "ambiguous", match_candidates: [{ presentation_id: "p2", score: 55, confidence: "medium" as const, evidence: [] }] };
    const tied = { ...base, media_import_id: "tied", match_state: "suggested", match_candidates: [{ presentation_id: "p3", score: 100, confidence: "high" as const, evidence: [] }, { presentation_id: "p4", score: 100, confidence: "high" as const, evidence: [] }] };
    expect(goodMatchIds([good, ambiguous, tied])).toEqual(["good"]);
    expect(goodMatchIds([good], "missing-name")).toEqual([]);
  });
});
