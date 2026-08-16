import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Events } from "../pages/central/Events";
import { SessionProvider } from "../state/session";

const eventId = "01900000-0000-7000-8000-000000000010";

function mockRequests(existingTimezone = "America/Chicago") {
  const writes: Array<{method?: string; body: Record<string, unknown>}> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = new URL(String(input), "http://test").pathname;
    if (path === "/api/v1/auth/session") return Response.json({authenticated:true,csrf_token:"csrf",expires_at:"2030-01-01T00:00:00Z",user:{user_id:"admin",username:"admin",display_name:"Admin",roles:["administrator"]}});
    if (path === "/api/v1/admin/events" && !init?.method) return Response.json([{event_id:eventId,name:"Annual Summit",timezone:existingTimezone,starts_at:"2027-05-01T00:00:00Z",ends_at:"2027-05-03T00:00:00Z",deployments:[]}]);
    if (path.startsWith("/api/v1/admin/events") && (init?.method === "POST" || init?.method === "PUT")) {
      writes.push({method:init.method,body:JSON.parse(String(init.body))});
      return Response.json({event_id:eventId,name:"Saved",timezone:"America/New_York",deployments:[]}, {status:init.method==="POST"?201:200});
    }
    throw new Error(`Unexpected ${init?.method??"GET"} ${path}`);
  });
  return writes;
}

test("creates and edits events in the shared modal with editable dates and timezone", async () => {
  const writes = mockRequests(); const user = userEvent.setup();
  render(<MemoryRouter><SessionProvider><Events/></SessionProvider></MemoryRouter>);
  expect(await screen.findByRole("button", {name:/create event/i})).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", {name:/create event/i}));
  let dialog = screen.getByRole("dialog");
  const createTimezone = within(dialog).getByLabelText("Timezone");
  expect(createTimezone).toHaveClass("themed-select__control");
  expect(createTimezone).toHaveTextContent("Central Time");
  await user.click(createTimezone);
  for (const [label, value] of [
    ["Eastern Time", "America/New_York"],
    ["Central Time", "America/Chicago"],
    ["Mountain Time", "America/Denver"],
    ["Mountain Time — Arizona", "America/Phoenix"],
    ["Pacific Time", "America/Los_Angeles"],
    ["Alaska Time", "America/Anchorage"],
    ["Hawaii Time", "Pacific/Honolulu"],
    ["Atlantic Time", "America/Halifax"],
    ["Newfoundland Time", "America/St_Johns"],
  ]) expect(within(dialog).getByRole("option", {name:label})).toHaveAttribute("data-value", value);
  await user.click(within(dialog).getByRole("option", {name:"Central Time"}));
  await user.type(within(dialog).getByLabelText("Event Name"), "  New Event  ");
  await user.type(within(dialog).getByLabelText("Start Date"), "2027-06-03");
  await user.type(within(dialog).getByLabelText("End Date"), "2027-06-01");
  await user.click(within(dialog).getByRole("button", {name:"Create"}));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("on or after");
  await user.clear(within(dialog).getByLabelText("End Date"));
  await user.type(within(dialog).getByLabelText("End Date"), "2027-06-05");
  await user.click(within(dialog).getByRole("button", {name:"Create"}));
  await waitFor(()=>expect(writes[0]).toMatchObject({method:"POST",body:{name:"New Event",timezone:"America/Chicago"}}));
  await waitFor(()=>expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await user.click(screen.getByRole("button", {name:"Edit"}));
  dialog = screen.getByRole("dialog");
  expect(within(dialog).getByLabelText("Event Name")).toHaveValue("Annual Summit");
  expect(within(dialog).getByLabelText("Start Date")).toHaveValue("2027-05-01");
  const editTimezone = within(dialog).getByLabelText("Timezone");
  expect(editTimezone).toHaveTextContent("Central Time");
  await user.click(editTimezone);
  await user.click(within(dialog).getByRole("option", {name:"Eastern Time"}));
  await user.clear(within(dialog).getByLabelText("Event Name"));
  await user.type(within(dialog).getByLabelText("Event Name"), "Updated Summit");
  await user.click(within(dialog).getByRole("button", {name:"Save Changes"}));
  await waitFor(()=>expect(writes[1]).toMatchObject({method:"PUT",body:{name:"Updated Summit",timezone:"America/New_York"}}));
});

test("preserves an existing global timezone outside the current UI catalog", async () => {
  const writes = mockRequests("Europe/London"); const user = userEvent.setup();
  render(<MemoryRouter><SessionProvider><Events/></SessionProvider></MemoryRouter>);
  await user.click(await screen.findByRole("button", {name:"Edit"}));
  const dialog = screen.getByRole("dialog");
  const timezone = within(dialog).getByLabelText("Timezone");
  expect(timezone).toHaveTextContent("Europe/London (existing timezone)");
  await user.click(timezone);
  expect(within(dialog).getByRole("option", {name:"Europe/London (existing timezone)"})).toHaveAttribute("aria-selected", "true");
  await user.click(within(dialog).getByRole("button", {name:"Save Changes"}));
  await waitFor(()=>expect(writes[0]).toMatchObject({method:"PUT",body:{timezone:"Europe/London"}}));
});

test("the shared themed selector supports keyboard selection across themes", async () => {
  mockRequests(); const user = userEvent.setup();
  render(<MemoryRouter><SessionProvider><Events/></SessionProvider></MemoryRouter>);
  await user.click(await screen.findByRole("button", {name:/create event/i}));
  const timezone = within(screen.getByRole("dialog")).getByLabelText("Timezone");
  document.documentElement.dataset.theme = "glass";
  expect(timezone).toHaveClass("themed-select__control");
  await user.click(timezone);
  await user.keyboard("{Escape}");
  await user.keyboard("{ArrowDown}{Enter}");
  expect(timezone).toHaveTextContent("Mountain Time");
  document.documentElement.dataset.theme = "classic";
  expect(timezone).toHaveClass("themed-select__control");
  await user.keyboard("{Space}{Escape}");
  expect(timezone).toHaveAttribute("aria-expanded", "false");
});
