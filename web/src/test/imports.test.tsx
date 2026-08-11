import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ImportBatch } from "../api/types";
import { Imports } from "../pages/central/Imports";
import { SessionProvider } from "../state/session";
import { MemoryRouter } from "react-router-dom";

const eventId = "01900000-0000-7000-8000-000000000010";

function batch(status: "ready" | "review"): ImportBatch {
  return {
    import_batch_id: `01900000-0000-7000-8000-0000000000${status === "ready" ? "11" : "12"}`,
    event_id: eventId,
    filename: status === "ready" ? "program-ready.xlsx" : "program-review.xlsx",
    status,
    row_count: status === "ready" ? 888 : 994,
    valid_count: status === "ready" ? 888 : 111,
    warning_count: status === "ready" ? 0 : 2,
    conflict_count: status === "ready" ? 0 : 7,
    committed_count: 0,
    rejected_count: status === "ready" ? 0 : 3,
    failure_summary: null,
    created_at: "2026-08-10T18:00:00Z",
    committed_at: null,
  };
}

function mockImportRequests(created: ImportBatch) {
  let importListRequests = 0;
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path === "/api/v1/auth/session") {
      return Response.json({
        authenticated: true,
        csrf_token: "test-csrf",
        expires_at: "2026-08-11T18:00:00Z",
        user: { user_id: "01900000-0000-7000-8000-000000000001", username: "admin", display_name: "Admin", roles: ["administrator"] },
      });
    }
    if (path === "/api/v1/admin/events") {
      return Response.json([
        {
          event_id: eventId,
          name: "Production Event",
          timezone: "America/Chicago",
          deployments: [],
        },
      ]);
    }
    if (path === `/api/v1/admin/events/${eventId}/imports` && init?.method === "POST") {
      return Response.json(created, { status: 201 });
    }
    if (path === `/api/v1/admin/events/${eventId}/imports`) {
      importListRequests += 1;
      return Response.json(importListRequests === 1 ? [] : [created]);
    }
    if (path === `/api/v1/admin/imports/${created.import_batch_id}`) {
      return Response.json({ ...created, rows: [], source_headers: [], detected_mapping: {}, sample_rows: [], preview_counts: {} });
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${path}`);
  });
}

async function stageImport(created: ImportBatch) {
  const fetchMock = mockImportRequests(created);
  const user = userEvent.setup();
  render(
    <MemoryRouter><SessionProvider><Imports /></SessionProvider></MemoryRouter>,
  );
  const input = await screen.findByLabelText("CSV or XLSX file");
  await user.upload(
    input,
    new File(["workbook"], created.filename, {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
  );
  expect((input as HTMLInputElement).files).toHaveLength(1);
  fireEvent.submit(input.closest("form")!);
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([, init]) => init?.method === "POST"),
    ).toBe(true),
  );
  await screen.findByText("Import staged successfully.");
  await waitFor(() => expect(screen.getByText(created.filename)).toBeInTheDocument());
  return fetchMock;
}

test("accepts a 201 ready import response and renders the refreshed batch", async () => {
  const created = batch("ready");
  const fetchMock = await stageImport(created);
  const row = screen.getByRole("row", { name: /program-ready\.xlsx/i });
  expect(row).toHaveTextContent("Ready");
  expect(row).toHaveTextContent("888");
  expect(within(row).getAllByText("0")).toHaveLength(3);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(
    fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"),
  ).toHaveLength(1);
});

test("renders review counts and useful review information after a 201 response", async () => {
  const created = batch("review");
  await stageImport(created);
  const row = screen.getByRole("row", { name: /program-review\.xlsx/i });
  expect(row).toHaveTextContent("Review");
  expect(row).toHaveTextContent("994");
  expect(row).toHaveTextContent("111");
  expect(row).toHaveTextContent("2");
  expect(row).toHaveTextContent("7");
  expect(row).toHaveTextContent("3");
  expect(row).toHaveTextContent("881 validation issue rows, 2 warnings, 7 conflicts");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
