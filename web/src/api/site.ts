import { siteClient } from "./client";
import type {
  Health,
  Row,
  SiteDeployment,
  SiteRegistration,
  SiteRoom,
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
  rooms: (signal?: AbortSignal) => get<SiteRoom[]>("/api/v1/rooms", signal),
  createRoom: (label: string) =>
    siteClient.request<SiteRoom>("/api/v1/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    }),
  retrySync: () =>
    siteClient.request<Row>("/api/v1/sync/retry-failed", { method: "POST" }),
};
