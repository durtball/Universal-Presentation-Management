import { useEffect, useState } from "react";
import type { DeletionOperation, DeletionPreview } from "../api/types";
import { ErrorSurface } from "./Feedback";

export function BulkPeopleDeletionDialog({ load, start, status, initialOperation, close, completed }:{
  load:()=>Promise<DeletionPreview>; start:(confirmation:string)=>Promise<DeletionOperation>;
  status:(id:string)=>Promise<DeletionOperation>; initialOperation?:DeletionOperation;
  close:()=>void; completed:()=>void;
}) {
  const [preview,setPreview]=useState<DeletionPreview>();
  const [confirmation,setConfirmation]=useState("");
  const [operation,setOperation]=useState(initialOperation);
  const [error,setError]=useState<unknown>();
  const [loadTarget]=useState(()=>load);
  useEffect(()=>{ if (!initialOperation) loadTarget().then(setPreview).catch(setError); },[initialOperation,loadTarget]);
  useEffect(()=>{
    if (!operation || !["pending","running","retry_wait"].includes(operation.status)) return;
    const timer=window.setInterval(()=>status(operation.deletion_operation_id).then(next=>{
      setOperation(next);
      if(next.status==="completed") completed();
    }).catch(setError),750);
    return ()=>window.clearInterval(timer);
  },[completed,operation,status]);
  const submit=async()=>{setError(undefined);try{setOperation(await start(confirmation));}catch(reason){setError(reason);}};
  const count=preview?.impact.people ?? operation?.dependency_counts.people ?? 0;
  const terminal=operation && !["pending","running","retry_wait"].includes(operation.status);
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-labelledby="bulk-delete-title">
    <h2 id="bulk-delete-title">Delete All</h2>
    <p className="danger-text"><strong>This operation cannot be undone.</strong> It will permanently delete {count} Permanent Person {count===1?"record":"records"}, retained person-owned history, and exclusively owned associated data. Shared assets and records referenced elsewhere remain subject to safe-reference rules.</p>
    {!operation && !preview && !error ? <p>Calculating impact…</p> : null}
    {operation ? <div className="notice" role="status"><strong>{operation.status==="completed"?"Deletion completed.":operation.status==="failed"?"Deletion needs attention.":"Deletion is processing."}</strong><p>Status: {operation.status} · {operation.stage}</p>{operation.last_error?<p>{operation.last_error}</p>:null}</div>:
      <label className="field">Type <strong>delete all</strong> to confirm<input className="input" autoComplete="off" value={confirmation} onChange={event=>setConfirmation(event.target.value)}/></label>}
    {error?<ErrorSurface error={error}/>:null}
    <div className="button-row"><button className="button" type="button" onClick={close}>{terminal?"Close":"Cancel"}</button>
      {!operation?<button className="button button--danger" type="button" disabled={!preview || count===0 || confirmation!=="delete all"} onClick={submit}>Delete All</button>:null}</div>
  </section></div>;
}
