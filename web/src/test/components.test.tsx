import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DataTable } from "../components/DataTable";
import { ErrorSurface, Loading } from "../components/Feedback";

test("table supports search, sorting, and keyboard-accessible selection", async () => {
  const user = userEvent.setup();
  const rows = [
    { id: "2", name: "Zulu" },
    { id: "1", name: "Alpha" },
  ];
  render(
    <DataTable
      rows={rows}
      columns={[{ key: "name", label: "Name", value: (row) => row.name }]}
      rowKey={(row) => row.id}
      label="Test records"
    />,
  );
  expect(screen.getAllByRole("row")[1]).toHaveTextContent("Alpha");
  await user.type(screen.getByRole("searchbox"), "Zulu");
  expect(screen.getByText("Zulu")).toBeInTheDocument();
  expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
  await user.click(screen.getByRole("checkbox", { name: /select row 2/i }));
  expect(screen.getByText("1 selected")).toBeInTheDocument();
});
test("loading and error surfaces are semantic", () => {
  const { rerender } = render(<Loading label="Program" />);
  expect(screen.getByRole("status")).toHaveTextContent("Program");
  rerender(<ErrorSurface error={new Error("boom")} />);
  expect(screen.getByRole("alert")).toBeInTheDocument();
});
