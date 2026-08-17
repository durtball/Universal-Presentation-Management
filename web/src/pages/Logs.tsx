import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import type { OperationalLog } from "../api/types";
import { Empty, ErrorSurface, Loading } from "../components/Feedback";
import { Page, Panel } from "../components/Page";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";
import { useSession } from "../state/session";

export function LogsPage({ mode }: { mode: "central" | "site" }) {
  const session = useSession();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState(params.get("search") || "");
  const [service, setService] = useState(params.get("service") || "");
  const [severity, setSeverity] = useState(params.get("severity") || "");
  const [minutes, setMinutes] = useState(Number(params.get("minutes") || 15));
  const [live, setLive] = useState(false);
  const [technical, setTechnical] = useState(false);
  const batchId = params.get("batch_id") || undefined;
  const mediaImportId = params.get("media_import_id") || undefined;
  const query = { search: search || undefined, service: service || undefined, severity: severity || undefined, minutes, limit: 100, batch_id: batchId, media_import_id: mediaImportId };
  const data = useApi((signal) => mode === "central" ? centralApi(session.csrfToken).logs(query, signal) : siteApi.logs(query, signal), [mode, search, service, severity, minutes, batchId, mediaImportId, session.csrfToken]);
  useEffect(() => { if (!live) return; const timer = window.setInterval(data.refresh, 5000); return () => window.clearInterval(timer); }, [live, data.refresh]);
  const update = () => { const next = new URLSearchParams(params); if (search) next.set("search", search); else next.delete("search"); if (service) next.set("service", service); else next.delete("service"); if (severity) next.set("severity", severity); else next.delete("severity"); next.set("minutes", String(minutes)); setParams(next); };
  const download = () => { const blob = new Blob([JSON.stringify(data.data?.items || [], null, 2)], { type: "application/json" }); const anchor = document.createElement("a"); anchor.href = URL.createObjectURL(blob); anchor.download = `${mode}-operational-logs.json`; anchor.click(); URL.revokeObjectURL(anchor.href); };
  return <Page eyebrow={mode === "central" ? "UPM Central Settings" : "UPM Site Settings"} title="Logs" description="Retention-managed operational diagnostics. Audit history remains separate.">
    <Panel title="Log filters" description="Queries are server-side, paginated, and safe for large histories."><div className="media-toolbar">
      <input className="input" aria-label="Search logs" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Message, event, or context" />
      <select className="input" aria-label="Service filter" value={service} onChange={(event) => setService(event.target.value)}><option value="">All services</option>{(mode === "central" ? ["central-api","central-worker","central-sync","media-storage","authentication","import-media","deployment-sync"] : ["site-api","site-worker","site-sync","site-media-storage","presentation-intake","room-delivery","diagnostics"]).map((value) => <option key={value}>{value}</option>)}</select>
      <select className="input" aria-label="Severity filter" value={severity} onChange={(event) => setSeverity(event.target.value)}><option value="">All severities</option>{["debug","info","warning","error","critical"].map((value) => <option key={value}>{value}</option>)}</select>
      <select className="input" aria-label="Time range" value={minutes} onChange={(event) => setMinutes(Number(event.target.value))}><option value={15}>Last 15 minutes</option><option value={60}>Last hour</option><option value={1440}>Last day</option><option value={43200}>Last 30 days</option></select>
      <button className="button" onClick={update}>Apply</button><button className="button" onClick={data.refresh}>Refresh</button><button className="button" onClick={() => setLive(!live)}>{live ? "Pause live" : "Live refresh"}</button><button className="button" onClick={() => setTechnical(!technical)}>{technical ? "Operator view" : "Technical view"}</button><button className="button" onClick={download}>Download</button>
    </div>{batchId && <p>Filtered batch: <code>{batchId}</code></p>}</Panel>
    {data.loading && !data.data ? <Loading /> : data.error ? <ErrorSurface error={data.error} onRetry={data.refresh} /> : data.data?.items.length ? <div className="log-list" aria-label="Operational logs">{data.data.items.map((item: OperationalLog) => <article key={item.operational_log_id}><time>{new Date(item.occurred_at).toLocaleString()}</time><StatusBadge value={item.severity} /><strong>{item.message}</strong><small>{item.service} · {item.event_type}</small>{technical && <pre>{JSON.stringify(item.context, null, 2)}</pre>}</article>)}</div> : <Empty title="No operational logs match these filters" />}
  </Page>;
}
