import { useEffect, useMemo, useState } from "react";
import { siteApi } from "../api/site";
import type { Row } from "../api/types";
import { Empty, ErrorSurface, Loading } from "../components/Feedback";
import { Page, Panel } from "../components/Page";
import { MediaStatusBadge, MediaUploadDialog, formatBytes, formatDate } from "../components/presentationMedia";
import { useApi } from "../hooks/useApi";

const PAGE_SIZE = 50;
type SiteSortKey = "filename"|"size"|"received"|"source"|"status"|"presenter"|"session"|"presentation"|"confidence";
export function SitePresentationMedia() {
  const [eventId, setEventId] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [checked, setChecked] = useState(new Set<string>());
  const [localRows, setLocalRows] = useState<Row[]>([]);
  const [filter, setFilter] = useState("needs_review");
  const [sort, setSort] = useState<{key:SiteSortKey;direction:"asc"|"desc"}>({key:"received",direction:"desc"});
  const [errors, setErrors] = useState(new Map<string, string>());
  const [rescanning, setRescanning] = useState(false);
  const [chosen, setChosen] = useState(new Map<string, Row>());
  const [lookupFor, setLookupFor] = useState<string>();
  const [lookupText, setLookupText] = useState("");
  const [lookupQuery, setLookupQuery] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const events = useApi((signal) => siteApi.deployments(signal), []);
  const registration = useApi((signal) => siteApi.registration(signal), []);
  const activeEvent = eventId || events.data?.[0]?.central_event_id || "";
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 300); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { const timer = setTimeout(() => setLookupQuery(lookupText), 300); return () => clearTimeout(timer); }, [lookupText]);
  const intake = useApi((signal) => activeEvent ? siteApi.mediaIntake(activeEvent, { search: query || undefined, disposition: filter === "rejected" ? "rejected" : "intake", limit: PAGE_SIZE, offset }, signal) : Promise.resolve({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 }), [activeEvent, query, offset, filter]);
  const pollIntake = intake.poll;
  useEffect(() => { if (!activeEvent) return; const refresh=()=>{if(document.visibilityState==="visible"&&!lookupFor&&!uploadOpen)pollIntake();}; const timer=window.setInterval(refresh,12000); document.addEventListener("visibilitychange",refresh); return()=>{window.clearInterval(timer);document.removeEventListener("visibilitychange",refresh);}; }, [activeEvent,pollIntake,lookupFor,uploadOpen]);
  const lookup = useApi((signal) => activeEvent && lookupQuery ? siteApi.presentationLookup(activeEvent, lookupQuery, signal) : Promise.resolve({ items: [] }), [activeEvent, lookupQuery]);
  useEffect(() => setLocalRows((current) => { const incoming=intake.data?.items??[]; const prior=new Map(current.map((row)=>[String(row.media_object_id),row])); return incoming.map((row)=>({...prior.get(String(row.media_object_id)),...row})); }), [intake.data?.items]);
  useEffect(() => { setChecked((current) => new Set([...current].filter((id) => { const row=localRows.find((item)=>String(item.media_object_id)===id); return row ? siteSelectable(row, chosen.get(id)) : false; }))); }, [chosen, localRows]);
  const rows = useMemo(() => localRows.filter((row) => siteReviewFilter(row, filter)).sort((left,right)=>compareSiteRows(left,right,sort,chosen)), [chosen, filter, localRows, sort]);
  const rescanPage = async () => { setRescanning(true); try { const updated = await siteApi.mediaIntake(activeEvent, { search: query || undefined, disposition: filter === "rejected" ? "rejected" : "intake", limit: PAGE_SIZE, offset }); setLocalRows(updated.items); } finally { setRescanning(false); } };
  const confirmed = async (items: {media_object_id: string; presentation_id: string}[]) => {
    if (!items.length) return;
    try {
      if (items.length === 1) await siteApi.confirmMedia(items[0].media_object_id, items[0].presentation_id);
      else await siteApi.confirmMediaBatch(items);
      const ids = new Set(items.map((item) => item.media_object_id));
      setLocalRows((current) => current.map((row) => ids.has(String(row.media_object_id)) ? { ...row, match_state: "confirmed" } : row));
      setChecked(new Set()); setChosen(new Map());
    } catch (error) {
      setErrors((current) => { const next=new Map(current); items.forEach((item)=>next.set(item.media_object_id,error instanceof Error?error.message:"Confirmation failed")); return next; });
    }
  };
  const rejectOne = async (mediaObjectId: string) => {
    const reason = window.prompt("Optional rejection reason") ?? undefined;
    try { await siteApi.rejectMedia(mediaObjectId, reason); setLocalRows((current) => current.filter((row) => String(row.media_object_id) !== mediaObjectId)); }
    catch (error) { setErrors((current) => new Map(current).set(mediaObjectId, error instanceof Error ? error.message : "Rejection failed")); }
  };
  const deleteOne = async (row: Row) => { const filename=String(row.filename); if(window.prompt(`Type the filename to delete this unconfirmed intake object:\n${filename}`)!==filename)return; const id=String(row.media_object_id); try{await siteApi.deleteMedia(id);setLocalRows((current)=>current.filter((item)=>String(item.media_object_id)!==id));}catch(error){setErrors((current)=>new Map(current).set(id,error instanceof Error?error.message:"Deletion failed"));} };
  const good = useMemo(() => rows.filter((row) => row.match_state === "suggested" && row.suggestion).map((row) => [String(row.media_object_id), String((row.suggestion as Row).presentation_id)] as const), [rows]);
  if (events.loading) return <Loading />;
  const uploadMedia = async (file: File, progress: (value:number)=>void, relativePath?:string, retrying?:(count:number)=>void) => {
    const result = await siteApi.uploadMedia(registration.data?.site_id || "", activeEvent, file, null, progress, relativePath, retrying);
    await intake.refresh();
    return { state: "needs_review" as const, sha256: result.content_hash, sizeBytes: result.size_bytes, availability: result.availability, failureReason: result.failure_reason };
  };
  return <Page eyebrow="Site-local intake" title="Presentation Media" description="Review staged media and explicitly confirm its canonical presentation. Candidate lookup is bounded and server-side." actions={<button className="button button--primary" disabled={!activeEvent || !registration.data?.site_id} onClick={()=>setUploadOpen(true)}>Upload Media</button>}>
    <div className="autonomy-banner"><strong>Operator-authoritative matching</strong><span>Suggestions are never confirmed automatically.</span></div>
    <Panel><div className="media-toolbar"><label>Event<select className="input" value={activeEvent} onChange={(e) => setEventId(e.target.value)}>{events.data?.map((event) => <option key={event.central_event_id} value={event.central_event_id}>{event.event_name || event.central_event_id}</option>)}</select></label><label>Search<input className="input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filename or path" /></label><label>Filter<select className="input" value={filter} onChange={(e)=>setFilter(e.target.value)}><option value="needs_review">Needs Review</option><option value="suggested">Suggested Match</option><option value="unmatched">No Match</option><option value="confirmed">Confirmed</option><option value="errors">Errors</option><option value="rejected">Rejected</option><option value="all">All</option></select></label><button className="button" disabled={rescanning} onClick={() => void rescanPage()}>Rescan All Unmatched</button><button className="button" onClick={() => setChecked((current)=>new Set([...current,...good.map(([id]) => id)]))}>Select All Good Matches</button><button className="button button--primary" disabled={!checked.size} onClick={() => void confirmed([...checked].map((media_object_id) => { const row = localRows.find((item) => String(item.media_object_id) === media_object_id); const candidate = chosen.get(media_object_id) ?? row?.suggestion as Row | undefined; return candidate ? {media_object_id, presentation_id: String(candidate.presentation_id)} : null; }).filter((item): item is {media_object_id: string; presentation_id: string} => Boolean(item)))}>Confirm selected ({checked.size})</button></div></Panel>
    {rescanning && <p role="status">Rescanning unmatched media — current page</p>}
    {intake.loading ? <Loading /> : intake.error ? <ErrorSurface error={intake.error} onRetry={intake.refresh} /> : !rows.length ? <Empty title="No media needs review" /> : <Panel title={`Active intake (${intake.data?.total ?? 0})`} description="One compact row per unconfirmed intake item."><div className="table-scroll"><table className="data-table"><thead><tr><th></th>{([["Filename","filename"],["File Size","size"],["Received","received"],["Source","source"],["Status","status"],["Suggested Presenter","presenter"],["Suggested Session","session"],["Suggested Presentation","presentation"],["Confidence","confidence"]] as [string,SiteSortKey][]).map(([label,key])=><th key={key}><button className="sort" aria-label={`Sort by ${label}`} onClick={()=>setSort({key,direction:sort.key===key&&sort.direction==="asc"?"desc":"asc"})}>{label} <span aria-hidden="true">{sort.key===key?(sort.direction==="asc"?"▲":"▼"):"↕"}</span></button></th>)}<th>Evidence / Actions</th></tr></thead><tbody>{rows.map((row) => { const id=String(row.media_object_id); const suggestion=row.suggestion as Row | null; const candidate=chosen.get(id) ?? suggestion; const presentationId=candidate ? String(candidate.presentation_id) : ""; return <tr key={id}><td><input type="checkbox" aria-label={`Select ${row.filename}`} checked={checked.has(id)} disabled={!presentationId || row.match_state === "confirmed"} onChange={() => setChecked((current) => { const next=new Set(current); if(next.has(id)) next.delete(id); else next.add(id); return next; })}/></td><td><strong>{String(row.filename)}</strong></td><td>{formatBytes(Number(row.size_bytes))}</td><td>{formatDate(row.received_at as string)}</td><td>{String(row.source || row.source_relative_path || "local intake")}</td><td><MediaStatusBadge value={String(row.match_state)} /></td><td>{candidate && Array.isArray(candidate.presenters) ? candidate.presenters.join(", ") : "—"}</td><td>{candidate ? <>{String(candidate.session || candidate.session_title || "—")}<small className="block">{formatDate(candidate.starts_at as string)} · {String(candidate.room || "—")}</small></> : "—"}</td><td><select className="input" aria-label={`Canonical match for ${row.filename}`} value={presentationId} disabled={!candidate || row.match_state === "confirmed"} onChange={() => undefined}><option value={presentationId}>{candidate ? `${String(candidate.presentation_identifier)} — ${String(candidate.title)}` : "Choose a presentation"}</option></select></td><td>{String(row.confidence || "none")}</td><td>{String(row.match_reason || "No unique identifier")}{row.disposition === "rejected" ? <strong className="block">Rejected</strong> : row.match_state === "confirmed" ? <strong className="block">Confirmed</strong> : <><button className="button button--small" onClick={() => {setLookupFor(id);setLookupText("");}}>Find…</button><button className="button button--small" disabled={rescanning} onClick={() => void rescanPage()}>Rescan</button><button className="button button--small button--primary" disabled={!presentationId} onClick={() => void confirmed([{media_object_id:id,presentation_id:presentationId}])}>Confirm</button><button className="button button--small" onClick={() => void rejectOne(id)}>Reject</button><button className="button button--small button--danger" onClick={() => void deleteOne(row)}>Delete</button></>}{errors.get(id)&&<small className="error-text block" role="alert">{errors.get(id)}</small>}</td></tr>; })}</tbody></table></div><div className="pagination"><button className="button" disabled={!offset} onClick={() => setOffset(Math.max(0,offset-PAGE_SIZE))}>Previous</button><span>{offset+1}–{Math.min(offset+PAGE_SIZE,intake.data?.total ?? 0)} of {intake.data?.total}</span><button className="button" disabled={offset+PAGE_SIZE >= (intake.data?.total ?? 0)} onClick={() => setOffset(offset+PAGE_SIZE)}>Next</button></div></Panel>}
    {lookupFor && <Panel title="Choose presenter, session, and presentation" description="Search results are limited to 25 canonical records."><label>Search Session / Presenter<input autoFocus className="input" value={lookupText} onChange={(e)=>setLookupText(e.target.value)} placeholder="Presentation ID, presenter, session, title, or room" /></label>{lookup.loading ? <Loading /> : <div className="log-list">{lookup.data?.items.map((candidate) => <button className="button" key={String(candidate.presentation_id)} onClick={() => {setChosen((current)=>new Map(current).set(lookupFor,candidate));setLookupFor(undefined);}}><strong>{String(candidate.presentation_identifier)} — {String(candidate.title)}</strong><small>{Array.isArray(candidate.presenters) ? candidate.presenters.join(", ") : ""} · {String(candidate.session || candidate.session_title || "")} · {String(candidate.room || "")}</small></button>)}</div>}<button className="button" onClick={()=>setLookupFor(undefined)}>Cancel</button></Panel>}
    {uploadOpen && <MediaUploadDialog title="Upload Media" upload={uploadMedia} onClose={()=>{setUploadOpen(false);intake.refresh();}} />}
  </Page>;
}

export function siteReviewFilter(row: Row, filter: string) { if (filter === "all") return true; if (filter === "rejected") return row.disposition === "rejected"; if (filter === "needs_review") return row.match_state !== "confirmed" && !row.failure_reason; if (filter === "suggested") return row.match_state === "suggested"; if (filter === "confirmed") return row.match_state === "confirmed"; if (filter === "unmatched") return ["unmatched", "ambiguous", "needs_review"].includes(String(row.match_state)); return Boolean(row.failure_reason); }
function siteSelectable(row: Row, chosen?: Row) { const candidate=chosen ?? row.suggestion as Row | undefined; return row.match_state!=="confirmed" && Boolean(candidate?.presentation_id); }
const siteConfidenceRank: Record<string,number>={none:0,low:1,medium:2,high:3};
function siteSortValue(row: Row,key:SiteSortKey,chosen:Map<string,Row>):string|number { const candidate=chosen.get(String(row.media_object_id)) ?? row.suggestion as Row|undefined; if(key==="size") return Number(row.size_bytes)||0; if(key==="received") return Date.parse(String(row.received_at||""))||0; if(key==="confidence") return siteConfidenceRank[String(row.confidence||"none")]||0; if(key==="filename") return String(row.filename||""); if(key==="source") return String(row.source_relative_path||row.source||""); if(key==="status") return String(row.match_state||""); if(key==="presenter") return candidate&&Array.isArray(candidate.presenters)?candidate.presenters.join(", "):""; if(key==="session") return String(candidate?.session||candidate?.session_title||""); return candidate?`${String(candidate.presentation_identifier)} ${String(candidate.title)}`:""; }
function compareSiteRows(left:Row,right:Row,sort:{key:SiteSortKey;direction:"asc"|"desc"},chosen:Map<string,Row>) { const a=siteSortValue(left,sort.key,chosen); const b=siteSortValue(right,sort.key,chosen); const compared=typeof a==="number"&&typeof b==="number"?a-b:String(a).localeCompare(String(b),undefined,{sensitivity:"base"}); return (compared||String(left.media_object_id).localeCompare(String(right.media_object_id)))*(sort.direction==="asc"?1:-1); }
