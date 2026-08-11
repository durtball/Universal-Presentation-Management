import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { centralApi } from "../../api/central";
import { PageState } from "../../components/Feedback";
import { useApi } from "../../hooks/useApi";
import { useSession } from "../../state/session";
import { EventDetail } from "./EventScoped";
import { AdminBoundary } from "./Shared";

export function EventDetailRoute() {
  const { eventId = "" } = useParams();
  const { csrfToken } = useSession();
  const api = useMemo(() => centralApi(csrfToken), [csrfToken]);
  const result = useApi(
    async (signal) =>
      (await api.events(signal)).find((event) => event.event_id === eventId),
    [api, eventId],
  );
  return (
    <AdminBoundary>
      <PageState {...result} onRetry={result.refresh}>
        {(event) => <EventDetail event={event} />}
      </PageState>
    </AdminBoundary>
  );
}
