import { useMemo } from "react";
import { centralApi } from "../../api/central";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { PageState } from "../../components/Feedback";
import { Metric, Page, Panel } from "../../components/Page";
import { StatusBadge } from "../../components/StatusBadge";
import { AdminBoundary, when } from "./Shared";

export function Dashboard() {
  const { adminToken } = useSession();
  const api = useMemo(() => centralApi(adminToken), [adminToken]);
  const result = useApi(
    async (signal) => {
      const [health, sites, events, people] = await Promise.all([
        api.health(signal),
        api.sites(signal),
        api.events(signal),
        api.people(signal),
      ]);
      return { health, sites, events, people };
    },
    [api],
  );
  return (
    <Page
      eyebrow="UPM Central"
      title="Operational overview"
      description="Live control-plane health and actionable synchronization state."
    >
      <AdminBoundary>
        <PageState {...result} onRetry={result.refresh}>
          {(data) => {
            const problems = data.sites.filter(
              (site) => site.connectivity !== "online" || site.failed_sync > 0,
            );
            return (
              <>
                <div className="metrics">
                  <Metric
                    label="Central API"
                    value={
                      <StatusBadge
                        value={
                          data.health.status === "foundation-ready"
                            ? "healthy"
                            : data.health.status
                        }
                      />
                    }
                    detail="Browser → Caddy → API"
                  />
                  <Metric
                    label="Registered sites"
                    value={data.sites.length}
                    detail={`${data.sites.filter((site) => site.connectivity === "online").length} online`}
                  />
                  <Metric
                    label="Events"
                    value={data.events.length}
                    detail="Central-owned programs"
                  />
                  <Metric
                    label="Permanent people"
                    value={data.people.length}
                    detail="Identities across all events"
                  />
                </div>
                <Panel
                  title="Actionable problems"
                  description="Offline, degraded, or failed synchronization states."
                >
                  {problems.length ? (
                    <ul className="issue-list">
                      {problems.map((site) => (
                        <li key={site.site_id}>
                          <div>
                            <strong>{site.display_name}</strong>
                            <small>
                              Last contact {when(site.last_seen_at)}
                            </small>
                          </div>
                          <StatusBadge
                            value={
                              site.failed_sync ? "failed" : site.connectivity
                            }
                          />
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="success-message">
                      No site connectivity or synchronization problems reported.
                    </p>
                  )}
                </Panel>
              </>
            );
          }}
        </PageState>
      </AdminBoundary>
    </Page>
  );
}
