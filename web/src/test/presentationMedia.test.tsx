import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MediaUploadDialog, PresentationMediaDetail, ReplicationStatus } from "../components/presentationMedia";

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
