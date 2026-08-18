import { useEffect, useMemo, useState } from "react";
import { siteApi } from "../api/site";
import type { Row } from "../api/types";
import { Empty, ErrorSurface, Loading } from "../components/Feedback";
import { Page, Panel } from "../components/Page";
import { MediaStatusBadge, formatBytes, formatDate } from "../components/presentationMedia";
import { useApi } from "../hooks/useApi";

const PAGE_SIZE = 50;
export function SitePresentationMedia() {
  const [eventId, setEventId] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState(new Map<string, string>());
  const [lookupFor, setLookupFor] = useState<string>();
  const [lookupText, setLookupText] = useState("");
  const [lookupQuery, setLookupQuery] = useState("");
  const events = useApi((signal) => siteApi.deployments(signal), []);
  const activeEvent = eventId || events.data?.[0]?.central_event_id || "";
  useEffect(() => { const timer = setTimeout(() => { setQuery(search); setOffset(0); }, 300); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { const timer = setTimeout(() => setLookupQuery(lookupText), 300); return () => clearTimeout(timer); }, [lookupText]);
  const intake = useApi((signal) => activeEvent ? siteApi.mediaIntake(activeEvent, { search: query || undefined, limit: PAGE_SIZE, offset }, signal) : Promise.resolve({ items: [], total: 0, limit: PAGE_SIZE, offset: 0 }), [activeEvent, query, offset]);
  const lookup = useApi((signal) => activeEvent && lookupQuery ? siteApi.presentationLookup(activeEvent, lookupQuery, signal) : Promise.resolve({ items: [] }), [activeEvent, lookupQuery]);
  const rows = useMemo(() => intake.data?.items ?? [], [intake.data?.items]);
  const confirmed = async (items: {media_object_id: string; presentation_id: string}[]) => {
    if (!items.length) return;
    if (items.length === 1) await siteApi.confirmMedia(items[0].media_object_id, items[0].presentation_id);
    else await siteApi.confirmMediaBatch(items);
    setSelected(new Map()); intake.refresh();
  };
  const good = useMemo(() => rows.filter((row) => row.match_state === "suggested" && row.suggestion).map((row) => [String(row.media_object_id), String((row.suggestion as Row).presentation_id)] as const), [rows]);
  if (events.loading) return <Loading />;
  return <Page eyebrow="Site-local intake" title="Presentation Media" description="Review staged media and explicitly confirm its canonical presentation. Candidate lookup is bounded and server-side.">
    <div className="autonomy-banner"><strong>Operator-authoritative matching</strong><span>Suggestions are never confirmed automatically.</span></div>
    <Panel><div className="media-toolbar"><label>Event<select className="input" value={activeEvent} onChange={(e) => setEventId(e.target.value)}>{events.data?.map((event) => <option key={event.central_event_id} value={event.central_event_id}>{event.event_name || event.central_event_id}</option>)}</select></label><label>Search<input className="input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filename or path" /></label><button className="button" onClick={() => setSelected(new Map(good))}>Select good suggestions</button><button className="button button--primary" disabled={!selected.size} onClick={() => void confirmed([...selected].map(([media_object_id, presentation_id]) => ({media_object_id, presentation_id})))}>Confirm selected ({selected.size})</button></div></Panel>
    {intake.loading ? <Loading /> : intake.error ? <ErrorSurface error={intake.error} onRetry={intake.refresh} /> : !rows.length ? <Empty title="No media needs review" /> : <Panel title={`Active intake (${intake.data?.total ?? 0})`} description="One compact row per unconfirmed intake item."><div className="table-scroll"><table className="data-table"><thead><tr><th></th><th>Filename / source</th><th>Detected evidence</th><th>Presenter</th><th>Session / time / room</th><th>Suggested presentation</th><th>Confidence</th><th>Actions</th></tr></thead><tbody>{rows.map((row) => { const id=String(row.media_object_id); const suggestion=row.suggestion as Row | null; const chosen=selected.get(id); return <tr key={id}><td><input type="checkbox" aria-label={`Select ${row.filename}`} checked={Boolean(chosen)} onChange={() => setSelected((current) => { const next=new Map(current); if(next.has(id)) next.delete(id); else if(suggestion) next.set(id,String(suggestion.presentation_id)); return next; })}/></td><td><strong>{String(row.filename)}</strong><small className="block">{String(row.source_relative_path || row.source || "local intake")} · {formatBytes(Number(row.size_bytes))}</small></td><td>{String(row.match_reason || "No unique identifier")}</td><td>{suggestion && Array.isArray(suggestion.presenters) ? suggestion.presenters.join(", ") : "—"}</td><td>{suggestion ? <>{String(suggestion.session || "—")}<small className="block">{formatDate(suggestion.starts_at as string)} · {String(suggestion.room || "—")}</small></> : "—"}</td><td>{suggestion ? `${String(suggestion.presentation_identifier)} — ${String(suggestion.title)}` : "Needs manual lookup"}</td><td><MediaStatusBadge value={String(row.match_state)} /><small className="block">{String(row.confidence || "none")}</small></td><td><button className="button button--small" onClick={() => {setLookupFor(id);setLookupText(String(row.filename || ""));}}>Find…</button><button className="button button--small button--primary" disabled={!chosen} onClick={() => void confirmed([{media_object_id:id,presentation_id:chosen!}])}>Confirm</button></td></tr>; })}</tbody></table></div><div className="pagination"><button className="button" disabled={!offset} onClick={() => setOffset(Math.max(0,offset-PAGE_SIZE))}>Previous</button><span>{offset+1}–{Math.min(offset+PAGE_SIZE,intake.data?.total ?? 0)} of {intake.data?.total}</span><button className="button" disabled={offset+PAGE_SIZE >= (intake.data?.total ?? 0)} onClick={() => setOffset(offset+PAGE_SIZE)}>Next</button></div></Panel>}
    {lookupFor && <Panel title="Choose presenter, session, and presentation" description="Search results are limited to 25 records."><label>Search<input autoFocus className="input" value={lookupText} onChange={(e)=>setLookupText(e.target.value)} /></label>{lookup.loading ? <Loading /> : <div className="log-list">{lookup.data?.items.map((candidate) => <button className="button" key={String(candidate.presentation_id)} onClick={() => {setSelected((current)=>new Map(current).set(lookupFor,String(candidate.presentation_id)));setLookupFor(undefined);}}><strong>{String(candidate.presentation_identifier)} — {String(candidate.title)}</strong><small>{Array.isArray(candidate.presenters) ? candidate.presenters.join(", ") : ""} · {String(candidate.session || "")}</small></button>)}</div>}<button className="button" onClick={()=>setLookupFor(undefined)}>Cancel</button></Panel>}
  </Page>;
}
