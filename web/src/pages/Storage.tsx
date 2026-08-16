import { useState } from "react";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import type { StorageChoice, StorageTarget } from "../api/types";
import { PageState } from "../components/Feedback";
import { Page, Panel } from "../components/Page";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";

const size = (value?: number) => value == null ? "Unavailable" : new Intl.NumberFormat(undefined, {
  style: "unit", unit: "byte", notation: "compact", unitDisplay: "narrow",
}).format(value);

function StorageCard({ root, targets, test, activate }: { root: StorageTarget; targets: StorageChoice[]; test: () => Promise<unknown>; activate: (id: string) => Promise<unknown> }) {
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string>();
  const [changing, setChanging] = useState(false);
  const [selected, setSelected] = useState(root.storage_target_id);
  const run = async () => { setTesting(true); setMessage(undefined); try {
    await test(); setMessage("Storage write/read/delete test passed.");
  } catch (error) { setMessage(error instanceof Error ? error.message : "Storage test failed.");
  } finally { setTesting(false); } };
  const used = root.percent_used ?? (root.used_bytes != null && root.total_bytes ? root.used_bytes * 100 / root.total_bytes : 0);
  return <Panel title={root.role === "staging" ? "Temporary / Staging Storage" : "Main Media Storage"}>
    <div className="storage-card">
      <div className="storage-card__heading"><strong>{root.display_name}</strong><StatusBadge value={root.health} /></div>
      <code className="storage-path">{root.path || "Path unavailable"}</code>
      <progress max="100" value={used} aria-label={`${used.toFixed(1)} percent used`} />
      <dl className="fact-grid">
        <div><dt>Total</dt><dd>{size(root.total_bytes)}</dd></div>
        <div><dt>Filesystem used</dt><dd>{size(root.used_bytes)}</dd></div>
        <div><dt>Available</dt><dd>{size(root.free_bytes)}</dd></div>
        <div><dt>UPM usage</dt><dd>{size(root.upm_owned_bytes)}</dd></div>
        <div><dt>{root.role === "staging" ? "Staged/importing files" : "Media objects"}</dt><dd>{root.object_count ?? 0}</dd></div>
        <div><dt>Last successful check</dt><dd>{root.last_successful_check_at ? new Date(root.last_successful_check_at).toLocaleString() : "Not yet"}</dd></div>
      </dl>
      {root.detail ? <div className="feedback feedback--warning" role="alert">{root.detail}</div> : null}
      {message ? <div className="feedback" role="status">{message}</div> : null}
      {changing ? <div className="storage-change" role="group" aria-label={`Change ${root.role} storage`}>
        <label>Available targets<select value={selected} onChange={event => setSelected(event.target.value)}>
          {targets.filter(target => target.role_compatibility.includes(root.role || "media")).map(target =>
            <option key={target.storage_target_id} value={target.storage_target_id}>{target.name} — {size(target.free_bytes)} free — {target.health}</option>)}</select></label>
        <div className="button-row"><button className="button button--primary" onClick={async () => {
          setTesting(true); setMessage(undefined); try { await activate(selected); setMessage("Storage target tested and activated."); setChanging(false); }
          catch (error) { setMessage(error instanceof Error ? error.message : "Storage activation failed."); } finally { setTesting(false); }
        }}>Validate &amp; Activate</button><button className="button button--quiet" onClick={() => setChanging(false)}>Cancel</button></div>
      </div> : null}
      <div className="button-row"><button className="button" disabled={testing || targets.length === 0} onClick={() => setChanging(true)}>Change Storage</button><button className="button" disabled={testing} onClick={run}>{testing ? "Testing…" : "Test Storage"}</button></div>
    </div>
  </Panel>;
}

export function StoragePage({ mode }: { mode: "central" | "site" }) {
  const result = useApi(async signal => mode === "central"
    ? await centralApi(null).storage(signal) : await siteApi.storage(signal), [mode]);
  return <Page eyebrow="Media infrastructure" title="Storage"
    description="Persistent deployment-local staging and immutable media capacity.">
    <PageState {...result} onRetry={result.refresh}>{overview => <>
      <div className="panel-grid panel-grid--two">{overview.roots.map(root => <StorageCard
        key={`${root.role}-${root.storage_target_id}`} root={root}
        targets={overview.targets}
        test={async () => {
          if (mode === "central") await centralApi(null).testStorage(root.role || "media");
          else await siteApi.testStorageRole(root.role || "media");
          result.refresh();
        }} activate={async id => {
          if (mode === "central") await centralApi(null).activateStorage(root.role || "media", id);
          else await siteApi.activateStorage(root.role || "media", id);
          result.refresh();
        }} />)}</div>
      <Panel title="Storage activity" description="Counts and byte totals come from durable media records.">
        <div className="fact-grid"><div><dt>Temporary bytes</dt><dd>{size(overview.roots.find(r => r.role === "staging")?.upm_owned_bytes)}</dd></div>
        <div><dt>Main UPM media bytes</dt><dd>{size(overview.roots.find(r => r.role === "media")?.upm_owned_bytes)}</dd></div></div>
      </Panel>
    </>}</PageState>
  </Page>;
}
