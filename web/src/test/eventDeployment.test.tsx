import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { EventDetail } from "../pages/central/EventScoped";
import { SessionProvider } from "../state/session";

const eventId="01900000-0000-7000-8000-000000000021";
const siteId="01900000-0000-7000-8000-000000000022";

test("previews and queues a production Event deployment",async()=>{
  let deployed=false;
  vi.spyOn(globalThis,"fetch").mockImplementation(async(input,init)=>{
    const url=new URL(String(input),"http://test"); const path=url.pathname;
    if(path==="/api/v1/auth/session")return Response.json({authenticated:true,csrf_token:"csrf",expires_at:"2026-08-17T00:00:00Z",user:{user_id:"admin",username:"admin",display_name:"Admin",roles:["administrator"]}});
    if(path==="/api/v1/admin/sites")return Response.json([{site_id:siteId,display_name:"Chicago Site",enrollment_state:"active",connectivity:"online",pending_sync:0,failed_sync:0}]);
    if(path===`/api/v1/admin/events/${eventId}/deployment-preview`)return Response.json({event_id:eventId,event_name:"Annual Summit",site_id:siteId,site_name:"Chicago Site",counts:{rooms:12,sessions:86,presenters:104,presentations:97},warnings:[{code:"session_without_room",message:"1 session has no imported room label."}],errors:[],deployable:true,existing_deployment_id:null,next_revision:1});
    if(path===`/api/v1/admin/events/${eventId}/deployments`&&init?.method==="POST"){deployed=true;return Response.json({deployment_id:"deployment-1"},{status:201});}
    throw new Error(`Unexpected ${init?.method??"GET"} ${path}`);
  });
  const changed=vi.fn(); const user=userEvent.setup();
  render(<MemoryRouter><SessionProvider><EventDetail event={{event_id:eventId,name:"Annual Summit",timezone:"UTC",deployments:[]}} onChanged={changed}/></SessionProvider></MemoryRouter>);
  await user.click(await screen.findByRole("button",{name:"Deploy to Site"}));
  await screen.findByText(/1 session has no imported room label/);
  expect(document.querySelector(".deployment-summary")).toHaveTextContent("12 rooms · 86 sessions · 104 presenters · 97 presentations");
  expect(screen.getByText(/Destination:/)).toHaveTextContent("Chicago Site");
  await user.click(screen.getByRole("button",{name:"Deploy Event"}));
  expect(deployed).toBe(true); expect(changed).toHaveBeenCalled();
});

test("blocks a structurally invalid deployment preview",async()=>{
  vi.spyOn(globalThis,"fetch").mockImplementation(async input=>{
    const path=new URL(String(input),"http://test").pathname;
    if(path==="/api/v1/auth/session")return Response.json({authenticated:true,csrf_token:"csrf",expires_at:"2026-08-17T00:00:00Z",user:{user_id:"admin",username:"admin",display_name:"Admin",roles:["administrator"]}});
    if(path==="/api/v1/admin/sites")return Response.json([{site_id:siteId,display_name:"Chicago Site",enrollment_state:"active",connectivity:"online",pending_sync:0,failed_sync:0}]);
    if(path===`/api/v1/admin/events/${eventId}/deployment-preview`)return Response.json({event_id:eventId,event_name:"Annual Summit",site_id:siteId,site_name:"Chicago Site",counts:{rooms:2,sessions:2,presenters:1,presentations:1},warnings:[],errors:[{code:"ambiguous_room_mapping",message:"1 imported room mapping is ambiguous."}],deployable:false,next_revision:1});
    throw new Error(`Unexpected ${path}`);
  });
  const user=userEvent.setup();
  render(<MemoryRouter><SessionProvider><EventDetail event={{event_id:eventId,name:"Annual Summit",timezone:"UTC",deployments:[]}}/></SessionProvider></MemoryRouter>);
  await user.click(await screen.findByRole("button",{name:"Deploy to Site"}));
  expect(await screen.findByText(/Cannot deploy: 1 imported room mapping is ambiguous/)).toBeInTheDocument();
  expect(screen.getByRole("button",{name:"Deploy Event"})).toBeDisabled();
});
