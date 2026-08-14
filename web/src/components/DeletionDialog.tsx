import { useEffect, useState } from "react";
import type { DeletionOperation, DeletionPreview } from "../api/types";
import { ErrorSurface } from "./Feedback";

export function DeletionDialog({ kind, name, load, start, close }:{
  kind:"Event"|"Permanent Person"; name:string;
  load:()=>Promise<DeletionPreview>; start:(confirmation:string)=>Promise<DeletionOperation>;
  close:()=>void;
}) {
  const [preview,setPreview]=useState<DeletionPreview>(); const [confirmation,setConfirmation]=useState("");
  const [operation,setOperation]=useState<DeletionOperation>(); const [error,setError]=useState<unknown>();
  const [loadTarget] = useState(() => load);
  useEffect(()=>{ loadTarget().then(setPreview).catch(setError); },[loadTarget]);
  const submit=async()=>{ setError(undefined); try { setOperation(await start(confirmation)); } catch(e){setError(e);} };
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title">
    <h2 id="delete-title">Delete {kind}</h2>
    <p className="danger-text"><strong>This is permanent.</strong> {kind === "Permanent Person" ? "Retained participation, identity, content history, relationships, and exclusively owned assets will be removed." : "Operational program data will be removed from Central and every deployed Site. Permanent Person identities and deliberately retained history remain."}</p>
    {!preview && !error ? <p>Calculating dependencies…</p> : null}
    {preview ? <dl className="impact-grid">{Object.entries(preview.impact).map(([key,value])=><div key={key}><dt>{key.replaceAll("_"," ")}</dt><dd>{value}</dd></div>)}</dl> : null}
    {operation ? <div className="notice"><strong>Deletion queued.</strong><p>Status: {operation.status} · {operation.stage}</p>
      {operation.site_statuses.map(site=><p key={site.site_id}>{site.display_name}: {site.status}</p>)}</div> : <label className="field">Type <strong>{name}</strong> to confirm<input className="input" autoComplete="off" value={confirmation} onChange={e=>setConfirmation(e.target.value)} /></label>}
    {error ? <ErrorSurface error={error}/> : null}
    <div className="button-row"><button className="button" type="button" onClick={close}>{operation ? "Close" : "Cancel"}</button>
      {!operation ? <button className="button button--danger" type="button" disabled={!preview || confirmation !== name} onClick={submit}>Start deletion</button> : null}</div>
  </section></div>;
}
