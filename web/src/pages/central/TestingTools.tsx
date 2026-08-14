import { useMemo, useState } from "react";
import { centralApi } from "../../api/central";
import { ErrorSurface, PageState } from "../../components/Feedback";
import { Page, Panel } from "../../components/Page";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { AdminBoundary } from "./Shared";

export function TestingTools() {
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const enabled = useApi((signal) => api.testingTools(signal), [api]);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<Error>();
  const [message, setMessage] = useState("");
  const run = async (kind: "people" | "data") => {
    setError(undefined); setMessage("");
    try {
      if (kind === "people") {
        const preview = await api.personPurgePreview();
        const summary = Object.entries(preview.affected_counts).map(([k, v]) => `${k}: ${v}`).join("\n");
        if (!window.confirm(`Permanent deletion preview:\n${summary}\n\nThis cannot be undone through the normal UI.`)) return;
        await api.purgeAllPeople(confirmation);
      } else await api.resetTestData(confirmation);
      setMessage("Destructive operation completed transactionally."); setConfirmation("");
    } catch (value) { setError(value instanceof Error ? value : new Error(String(value))); }
  };
  return <Page eyebrow="Development / testing only" title="Testing Tools / Danger Zone" description="Privileged clean-slate controls for importer testing. These controls are hidden when the server feature flag is disabled.">
    <AdminBoundary><PageState {...enabled} onRetry={enabled.refresh}>{(flag) => flag.enabled ? <>
      <Panel title="Danger zone" description="Preview the impact, then type the exact phrase. These actions cannot be undone through the normal UI.">
        <label>Confirmation phrase <input aria-label="Confirmation phrase" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label>
        <div className="actions">
          <button className="danger" disabled={confirmation !== "DELETE ALL SPEAKERS"} onClick={() => run("people")}>Delete All Permanent Speakers</button>
          <button className="danger" disabled={confirmation !== "RESET TEST DATA"} onClick={() => run("data")}>Reset All Test Program Data</button>
        </div>
        <p className="muted">Media, Sites, devices, users, authentication, deployment configuration, and infrastructure are preserved.</p>
        {message && <p role="status">{message}</p>}{error && <ErrorSurface error={error} />}
      </Panel>
    </> : <Panel title="Testing tools disabled" description="Set UPM_CENTRAL_ENABLE_DESTRUCTIVE_TEST_TOOLS=true only in a development/testing deployment."><p className="muted">Normal production-safe event and identity management remains available.</p></Panel>}</PageState></AdminBoundary>
  </Page>;
}
