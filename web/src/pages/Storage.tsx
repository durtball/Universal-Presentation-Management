import { useState } from "react";
import { centralApi } from "../api/central";
import { siteApi } from "../api/site";
import type { StorageTarget } from "../api/types";
import { PageState } from "../components/Feedback";
import { Page, Panel } from "../components/Page";
import { StatusBadge } from "../components/StatusBadge";
import { useApi } from "../hooks/useApi";

const size = (value?: number) => value == null ? "Unavailable" : new Intl.NumberFormat(undefined, {
  style: "unit", unit: "byte", notation: "compact", unitDisplay: "narrow",
}).format(value);

function StorageCard({ root, test }: { root: StorageTarget; test: () => Promise<unknown> }) {
  const [testing, setTesting] = useState(false);
  const [message, setMessage] = useState<string>();
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
        <div><dt>Used</dt><dd>{size(root.used_bytes)}</dd></div>
        <div><dt>Available</dt><dd>{size(root.free_bytes)}</dd></div>
        <div><dt>UPM usage</dt><dd>{size(root.upm_owned_bytes)}</dd></div>
        <div><dt>{root.role === "staging" ? "Staged/importing files" : "Media objects"}</dt><dd>{root.object_count ?? 0}</dd></div>
        <div><dt>Last successful check</dt><dd>{root.last_successful_check_at ? new Date(root.last_successful_check_at).toLocaleString() : "Not yet"}</dd></div>
      </dl>
      {root.detail ? <div className="feedback feedback--warning" role="alert">{root.detail}</div> : null}
      {message ? <div className="feedback" role="status">{message}</div> : null}
      <div className="button-row"><button className="button" disabled={testing} onClick={run}>{testing ? "Testing…" : "Test Storage"}</button></div>
    </div>
  </Panel>;
}

export function StoragePage({ mode }: { mode: "central" | "site" }) {
  const result = useApi(async signal => mode === "central"
    ? (await centralApi(null).storage(signal)).roots : await siteApi.storage(signal), [mode]);
  return <Page eyebrow="Media infrastructure" title="Storage"
    description="Persistent deployment-local staging and immutable media capacity.">
    <PageState {...result} onRetry={result.refresh}>{roots => <>
      <div className="panel-grid panel-grid--two">{roots.map(root => <StorageCard
        key={`${root.role}-${root.storage_target_id}`} root={root}
        test={async () => {
          if (mode === "central") await centralApi(null).testStorage(root.role || "media");
          else await siteApi.testStorage(root.storage_target_id);
          result.refresh();
        }} />)}</div>
      <Panel title="Storage activity" description="Counts and byte totals come from durable media records.">
        <div className="fact-grid"><div><dt>Temporary bytes</dt><dd>{size(roots.find(r => r.role === "staging")?.upm_owned_bytes)}</dd></div>
        <div><dt>Main UPM media bytes</dt><dd>{size(roots.find(r => r.role === "media")?.upm_owned_bytes)}</dd></div></div>
      </Panel>
    </>}</PageState>
  </Page>;
}
