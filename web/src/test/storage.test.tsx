import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StoragePage } from "../pages/Storage";

test("storage distinguishes filesystem and UPM usage and lists compatible targets", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    service_available: true,
    roots: [{
      storage_target_id: "staging-id",
      role: "staging",
      display_name: "Central Staging Storage",
      path: "/storage/staging",
      available: true,
      writable: true,
      health: "Healthy",
      total_bytes: 1000,
      used_bytes: 100,
      free_bytes: 900,
      upm_owned_bytes: 25,
      object_count: 1,
    }],
    targets: [{
      storage_target_id: "staging-id",
      name: "Central Staging Storage",
      internal_path: "/storage/staging",
      role_compatibility: ["staging"],
      health: "Healthy",
      writable: true,
      free_bytes: 900,
    }, {
      storage_target_id: "media-id",
      name: "Central Media Storage",
      internal_path: "/storage/media",
      role_compatibility: ["media"],
      health: "Healthy",
      writable: true,
      free_bytes: 900,
    }],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));

  render(<StoragePage mode="central" />);

  expect(await screen.findByText("Filesystem used")).toBeInTheDocument();
  expect(screen.getByText("UPM usage")).toBeInTheDocument();
  expect(screen.getByText("/storage/staging")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Change Storage" }));
  expect(screen.getByRole("option", { name: /Central Staging Storage/ })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Central Media Storage/ })).not.toBeInTheDocument();
});
