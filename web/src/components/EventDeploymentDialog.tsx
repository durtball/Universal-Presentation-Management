import { useEffect, useState } from "react";
import type { EventDeploymentPreview, SiteRecord } from "../api/types";
import { ErrorSurface } from "./Feedback";

export function EventDeploymentDialog({eventName,sites,loadPreview,deploy,push,close,completed}:{
  eventName:string; sites:SiteRecord[];
  loadPreview:(siteId:string)=>Promise<EventDeploymentPreview>;
  deploy:(siteId:string)=>Promise<unknown>; push:(deploymentId:string)=>Promise<unknown>;
  close:()=>void; completed:()=>void;
}) {
  const eligible=sites.filter(site=>site.enrollment_state==="active");
  const [siteId,setSiteId]=useState(eligible[0]?.site_id??"");
  const [preview,setPreview]=useState<EventDeploymentPreview>();
  const [error,setError]=useState<unknown>(); const [saving,setSaving]=useState(false);
  useEffect(()=>{if(!siteId)return;setPreview(undefined);setError(undefined);loadPreview(siteId).then(setPreview).catch(setError);},[loadPreview,siteId]);
  const submit=async()=>{if(!preview)return;setSaving(true);setError(undefined);try{
    if(preview.existing_deployment_id)await push(preview.existing_deployment_id);else await deploy(siteId);
    completed();
  }catch(reason){setError(reason);}finally{setSaving(false);}};
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="deploy-title">
    <h2 id="deploy-title">Deploy to Site</h2><p><strong>{eventName}</strong></p>
    <label className="field">Destination Site<select className="input" value={siteId} onChange={event=>setSiteId(event.target.value)}>{eligible.map(site=><option key={site.site_id} value={site.site_id}>{site.display_name}</option>)}</select></label>
    {!preview&&!error?<p>Preparing deployment summary…</p>:null}
    {preview?<><p className="deployment-summary"><strong>{preview.counts.rooms}</strong> rooms · <strong>{preview.counts.sessions}</strong> sessions · <strong>{preview.counts.presenters}</strong> presenters · <strong>{preview.counts.presentations}</strong> presentations</p><p>Destination: <strong>{preview.site_name}</strong> · Version {preview.next_revision}</p>
      {preview.warnings.map(item=><p className="notice" key={item.code}>Warning: {item.message}</p>)}
      {preview.errors.map(item=><p className="danger-text" key={item.code}>Cannot deploy: {item.message}</p>)}</>:null}
    {error?<ErrorSurface error={error}/>:null}
    <div className="button-row"><button className="button" type="button" onClick={close}>Cancel</button><button className="button button--primary" type="button" disabled={!preview?.deployable||saving} onClick={submit}>{preview?.existing_deployment_id?"Deploy Update":"Deploy Event"}</button></div>
  </section></div>;
}
