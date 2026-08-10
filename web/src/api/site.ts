import { siteClient } from "./client";
import type {
  Health,
  Row,
  SiteDeployment,
  SiteRegistration,
  StorageTarget,
} from "./types";
const get = <T>(path: string, signal?: AbortSignal) =>
  siteClient.request<T>(path, { signal, retry: true });
export const siteApi = {
  health: (signal?: AbortSignal) => get<Health>("/health", signal),
  registration: (signal?: AbortSignal) =>
    get<SiteRegistration>("/api/v1/central-registration", signal),
  deployments: (signal?: AbortSignal) =>
    get<SiteDeployment[]>("/api/v1/event-deployments", signal),
  storage: (signal?: AbortSignal) =>
    get<StorageTarget[]>("/api/v1/storage-targets/health", signal),
  program: (eventId: string, signal?: AbortSignal) =>
    get<Row>(`/api/v1/events/${eventId}/program`, signal),
  retrySync: () =>
    siteClient.request<Row>("/api/v1/sync/retry-failed", { method: "POST" }),
};
