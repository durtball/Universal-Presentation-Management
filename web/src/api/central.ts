import { createCentralClient } from "./client";
import type {
  EventRecord,
  Health,
  ImportBatch,
  PersonRecord,
  Row,
  SiteRecord,
} from "./types";

export function centralApi(token: string | null) {
  const client = createCentralClient(token);
  const get = <T>(path: string, signal?: AbortSignal) =>
    client.request<T>(path, { signal, retry: true });
  return {
    health: (signal?: AbortSignal) => get<Health>("/health", signal),
    sites: (signal?: AbortSignal) =>
      get<SiteRecord[]>("/api/v1/admin/sites", signal),
    events: (signal?: AbortSignal) =>
      get<EventRecord[]>("/api/v1/admin/events", signal),
    people: (signal?: AbortSignal) =>
      get<PersonRecord[]>("/api/v1/admin/people", signal),
    participants: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/participants`, signal),
    sessions: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/sessions`, signal),
    presentations: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/presentations`, signal),
    imports: (eventId: string, signal?: AbortSignal) =>
      get<ImportBatch[]>(`/api/v1/admin/events/${eventId}/imports`, signal),
    importDetail: (batchId: string, signal?: AbortSignal) =>
      get<ImportBatch>(`/api/v1/admin/imports/${batchId}`, signal),
    uploadImport: (eventId: string, file: File) => {
      const form = new FormData();
      form.append("file", file);
      return client.request<ImportBatch>(`/api/v1/admin/events/${eventId}/imports`, {
        method: "POST",
        body: form,
        timeoutMs: 60_000,
      });
    },
    changeSiteState: (siteId: string, action: string) =>
      client.request<Row>(`/api/v1/admin/sites/${siteId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
  };
}
