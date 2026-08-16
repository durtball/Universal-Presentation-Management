import { createCentralClient } from "./client";
import type {
  AuthSession,
  EventRecord,
  Health,
  ImportBatch,
  PersonRecord,
  DeletionOperation,
  DeletionPreview,
  Row,
  RoomMapping,
  SiteRecord,
} from "./types";

export function centralApi(csrfToken: string | null = null) {
  const client = createCentralClient(csrfToken);
  const get = <T>(path: string, signal?: AbortSignal) =>
    client.request<T>(path, { signal, retry: true });
  return {
    login: (username: string, password: string) =>
      client.request<AuthSession>("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      }),
    session: (signal?: AbortSignal) =>
      get<AuthSession>("/api/v1/auth/session", signal),
    logout: () =>
      client.request<void>("/api/v1/auth/logout", { method: "POST" }),
    health: (signal?: AbortSignal) => get<Health>("/health", signal),
    sites: (signal?: AbortSignal) =>
      get<SiteRecord[]>("/api/v1/admin/sites", signal),
    events: (signal?: AbortSignal) =>
      get<EventRecord[]>("/api/v1/admin/events", signal),
    createEvent: (name: string, timezone: string) =>
      client.request<EventRecord>("/api/v1/admin/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, timezone }),
      }),
    eventDeletionImpact: (eventId: string) =>
      get<DeletionPreview>(`/api/v1/admin/events/${eventId}/deletion-impact`),
    deleteEvent: (eventId: string, confirmation: string) =>
      client.request<DeletionOperation>(`/api/v1/admin/events/${eventId}`, {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation }),
      }),
    deployEvent: (eventId: string, siteId: string) =>
      client.request<Row>(`/api/v1/admin/events/${eventId}/deployments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site_id: siteId }),
      }),
    pushDeployment: (deploymentId: string) =>
      client.request<Row>(`/api/v1/admin/event-deployments/${deploymentId}/push`, {
        method: "POST",
      }),
    people: (signal?: AbortSignal) =>
      get<PersonRecord[]>("/api/v1/admin/people", signal),
    personDeletionImpact: (personId: string) =>
      get<DeletionPreview>(`/api/v1/admin/people/${personId}/lifecycle-deletion-impact`),
    deletePerson: (personId: string, confirmation: string) =>
      client.request<DeletionOperation>(`/api/v1/admin/people/${personId}/lifecycle`, {
        method: "DELETE", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation }),
      }),
    bulkPeopleDeletionImpact: () =>
      get<DeletionPreview>("/api/v1/admin/people-bulk-deletion/impact"),
    deleteAllPeople: (confirmation: string) =>
      client.request<DeletionOperation>("/api/v1/admin/people-bulk-deletion", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation }),
      }),
    currentBulkPeopleDeletion: () =>
      get<DeletionOperation | null>("/api/v1/admin/people-bulk-deletion/current"),
    deletionStatus: (operationId: string) =>
      get<DeletionOperation>(`/api/v1/admin/deletions/${operationId}`),
    participants: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/participants`, signal),
    sessions: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/sessions`, signal),
    presentations: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/presentations`, signal),
    roomMappings: (eventId: string, siteId: string, signal?: AbortSignal) =>
      client.request<RoomMapping[]>(`/api/v1/admin/events/${eventId}/room-mappings`, {
        signal,
        retry: true,
        query: { site_id: siteId },
      }),
    saveRoomMapping: (mapping: Row) =>
      client.request<RoomMapping>("/api/v1/admin/room-mappings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mapping),
      }),
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
    reconcileImportRow: (
      rowId: string,
      action: string,
      selectedPersonId?: string,
    ) =>
      client.request<Row>(`/api/v1/admin/import-rows/${rowId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action,
          selected_person_id: selectedPersonId || null,
          reason: "Resolved through Central import review",
        }),
      }),
    commitImport: (batchId: string) =>
      client.request<ImportBatch>(`/api/v1/admin/imports/${batchId}/commit`, {
        method: "POST",
      }),
    changeSiteState: (siteId: string, action: string) =>
      client.request<Row>(`/api/v1/admin/sites/${siteId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      }),
  };
}
