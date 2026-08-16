import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { People } from "../pages/central/People";
import { SessionProvider } from "../state/session";

const operation = {
  deletion_operation_id: "01900000-0000-7000-8000-000000000099",
  target_type: "people_bulk" as const,
  target_display_name: "All Permanent People",
  status: "pending",
  stage: "queued",
  dependency_counts: { people: 2 },
  site_statuses: [],
};
const people = [
  { person_id: "01900000-0000-7000-8000-000000000001", display_name: "Ada One" },
  { person_id: "01900000-0000-7000-8000-000000000002", display_name: "Grace Two" },
];

function renderPeople(fetcher: typeof fetch) {
  vi.spyOn(globalThis, "fetch").mockImplementation(fetcher);
  return render(<MemoryRouter><SessionProvider><People /></SessionProvider></MemoryRouter>);
}

function responseForCommon(path: string) {
  if (path === "/api/v1/auth/session") return Response.json({authenticated:true,csrf_token:"csrf",expires_at:"2026-08-17T00:00:00Z",user:{user_id:"admin",username:"admin",display_name:"Admin",roles:["administrator"]}});
  return undefined;
}

test("requires the exact phrase and refreshes after successful Delete All", async () => {
  let completed=false;
  renderPeople(async (input,init)=>{
    const path=new URL(String(input),"http://test").pathname;
    const common=responseForCommon(path); if(common)return common;
    if(path==="/api/v1/admin/people")return Response.json(completed?[]:people);
    if(path==="/api/v1/admin/people-bulk-deletion/current")return Response.json(null);
    if(path==="/api/v1/admin/people-bulk-deletion/impact")return Response.json({confirmation:"delete all",impact:{people:2,retained_history:1}});
    if(path==="/api/v1/admin/people-bulk-deletion"&&init?.method==="POST")return Response.json(operation,{status:202});
    if(path===`/api/v1/admin/deletions/${operation.deletion_operation_id}`){completed=true;return Response.json({...operation,status:"completed",stage:"completed"});}
    throw new Error(`Unexpected ${init?.method??"GET"} ${path}`);
  });
  const user=userEvent.setup();
  await user.click(await screen.findByRole("button",{name:"Delete All"}));
  expect(await screen.findByText(/permanently delete 2 Permanent Person records/i)).toBeInTheDocument();
  const confirm=screen.getByLabelText(/Type delete all/i);
  const submit=screen.getAllByRole("button",{name:"Delete All"}).at(-1)!;
  await user.type(confirm,"Delete All"); expect(submit).toBeDisabled();
  await user.clear(confirm); await user.type(confirm,"delete all"); expect(submit).toBeEnabled();
  await user.click(submit);
  expect(await screen.findByText("Deletion is processing.")).toBeInTheDocument();
  expect(await screen.findByText("Deletion completed.",{},{timeout:3000})).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByText(/no Permanent People to delete/i)).toBeInTheDocument());
});

test("disables Delete All for an empty People list", async () => {
  renderPeople(async input=>{
    const path=new URL(String(input),"http://test").pathname;
    const common=responseForCommon(path); if(common)return common;
    if(path==="/api/v1/admin/people")return Response.json([]);
    if(path==="/api/v1/admin/people-bulk-deletion/current")return Response.json(null);
    throw new Error(`Unexpected ${path}`);
  });
  expect(await screen.findByRole("button",{name:"Delete All"})).toBeDisabled();
  expect(screen.getByText(/no Permanent People to delete/i)).toBeInTheDocument();
});

test("surfaces a safe API error without reporting success", async () => {
  renderPeople(async (input,init)=>{
    const path=new URL(String(input),"http://test").pathname;
    const common=responseForCommon(path); if(common)return common;
    if(path==="/api/v1/admin/people")return Response.json(people);
    if(path==="/api/v1/admin/people-bulk-deletion/current")return Response.json(null);
    if(path==="/api/v1/admin/people-bulk-deletion/impact")return Response.json({confirmation:"delete all",impact:{people:2}});
    if(path==="/api/v1/admin/people-bulk-deletion"&&init?.method==="POST")return Response.json({detail:"Deletion could not be queued"},{status:500});
    throw new Error(`Unexpected ${path}`);
  });
  const user=userEvent.setup(); await user.click(await screen.findByRole("button",{name:"Delete All"}));
  await user.type(await screen.findByLabelText(/Type delete all/i),"delete all");
  await user.click(screen.getAllByRole("button",{name:"Delete All"}).at(-1)!);
  expect(await screen.findByRole("alert")).toHaveTextContent("Deletion could not be queued");
  expect(screen.queryByText("Deletion completed.")).not.toBeInTheDocument();
});

test("restores a running operation after page refresh", async () => {
  renderPeople(async input=>{
    const path=new URL(String(input),"http://test").pathname;
    const common=responseForCommon(path); if(common)return common;
    if(path==="/api/v1/admin/people")return Response.json(people);
    if(path==="/api/v1/admin/people-bulk-deletion/current")return Response.json({...operation,status:"running",stage:"central_cleanup"});
    if(path===`/api/v1/admin/deletions/${operation.deletion_operation_id}`)return Response.json({...operation,status:"running",stage:"central_cleanup"});
    throw new Error(`Unexpected ${path}`);
  });
  expect(await screen.findByRole("heading",{name:"Delete All"})).toBeInTheDocument();
  expect(screen.getByText("Deletion is processing.")).toBeInTheDocument();
});
