import { createCentralClient } from "./client";
import { uploadFile } from "./upload";
import type {
  AuthSession,
  EventRecord,
  EventDeploymentPreview,
  EventWrite,
  Health,
  ImportBatch,
  PersonRecord,
  DeletionOperation,
  DeletionPreview,
  Row,
  RoomMapping,
  SiteRecord,
  CentralMediaImport,
  CentralMediaWorkspace,
  PresentationMatchCandidate,
  StorageOverview,
  UserRecord,
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
    users: (signal?: AbortSignal) => get<UserRecord[]>("/api/v1/admin/users", signal),
    createUser: (values: Record<string, unknown>) => client.request<UserRecord>("/api/v1/admin/users", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(values)}),
    setSmbPassword: (id:string,password:string) => client.request(`/api/v1/admin/users/${id}/smb-password`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({password})}),
    revokeSmb: (id:string) => client.request<void>(`/api/v1/admin/users/${id}/smb-access`,{method:"DELETE"}),
    health: (signal?: AbortSignal) => get<Health>("/health", signal),
    storage: (signal?: AbortSignal) => get<StorageOverview>("/api/v1/admin/storage", signal),
    testStorage: (role: string) => client.request<Row>(`/api/v1/admin/storage/${role}/test`, { method: "POST" }),
    activateStorage: (role: string, id: string) => client.request<Row>(
      `/api/v1/admin/storage/${role}/${id}`, { method: "PUT" }),
    changeStaging: (path: string) => client.request<Row>("/api/v1/admin/storage/staging", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    }),
    sites: (signal?: AbortSignal) =>
      get<SiteRecord[]>("/api/v1/admin/sites", signal),
    events: (signal?: AbortSignal) =>
      get<EventRecord[]>("/api/v1/admin/events", signal),
    createEvent: (values: EventWrite) =>
      client.request<EventRecord>("/api/v1/admin/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      }),
    updateEvent: (eventId: string, values: EventWrite) =>
      client.request<EventRecord>(`/api/v1/admin/events/${eventId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
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
    deploymentPreview: (eventId: string, siteId: string, signal?: AbortSignal) =>
      client.request<EventDeploymentPreview>(
        `/api/v1/admin/events/${eventId}/deployment-preview`,
        { signal, retry: true, query: { site_id: siteId } },
      ),
    pushDeployment: (deploymentId: string) =>
      client.request<Row>(`/api/v1/admin/event-deployments/${deploymentId}/push`, {
        method: "POST",
      }),
    retryDeployment: (deploymentId: string) =>
      client.request<Row>(`/api/v1/admin/event-deployments/${deploymentId}/retry`, {
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
    retryDeletion: (operationId: string) =>
      client.request<DeletionOperation>(`/api/v1/admin/deletions/${operationId}/retry`, {
        method: "POST",
      }),
    participants: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/participants`, signal),
    sessions: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/sessions`, signal),
    presentations: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/presentations`, signal),
    mediaWorkspace: (eventId: string, signal?: AbortSignal) =>
      get<CentralMediaWorkspace>(`/api/v1/admin/events/${eventId}/media-imports`, signal),
    mediaCandidates: (eventId: string, search = "", signal?: AbortSignal, presentationIds: string[] = []) =>
      client.request<{ candidates: PresentationMatchCandidate[] }>(`/api/v1/admin/events/${eventId}/presentation-match-candidates`, { signal, retry: true, query: { search: search || undefined, presentation_ids: presentationIds.length ? presentationIds.join(",") : undefined } }),
    createMediaBatch: (eventId: string, selectedCount: number, skippedItems: Array<{path:string;reason:string}>) =>
      client.request<import("./types").MediaImportBatch>(`/api/v1/admin/events/${eventId}/media-import-batches`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ selected_count: selectedCount, skipped_items: skippedItems }) }),
    mediaBatches: (eventId: string) => get<{items: import("./types").MediaImportBatch[]}>(`/api/v1/admin/events/${eventId}/media-import-batches`),
    uploadMedia: (eventId: string, file: File, onProgress: (value: number) => void, relativePath?: string, onRetry?: (count: number) => void, batchId?: string) =>
      uploadFile<CentralMediaImport>({ path: `/api/v1/admin/events/${eventId}/media-imports`, file, progress: onProgress, csrf: csrfToken, relativePath, retrying: onRetry, batchId }),
    logs: (query: Record<string, string | number | undefined>, signal?: AbortSignal) => client.request<{items: import("./types").OperationalLog[]; next_cursor?: string | null}>("/api/v1/admin/logs", { query, signal, retry: true }),
    assignMedia: (importId: string, presentationId: string) =>
      client.request<CentralMediaImport>(
        `/api/v1/admin/media-imports/${importId}/assignment/${presentationId}`,
        { method: "PUT" },
      ),
    confirmMedia: (items: Array<{ media_import_id: string; presentation_id: string }>) =>
      client.request<{ results: Array<{ media_import_id: string; status: string; message?: string }> }>("/api/v1/admin/media-imports/confirmations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items }) }),
    refreshMediaMatch: (importId: string) => client.request<CentralMediaImport>(`/api/v1/admin/media-imports/${importId}/match`, { method: "POST" }),
    rejectMedia: (importId: string, reason?: string) => client.request<CentralMediaImport>(`/api/v1/admin/media-imports/${importId}/reject`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason || null }) }),
    rescanUnmatchedMedia: (eventId: string) => client.request<import("./types").MediaRescanProgress>(`/api/v1/admin/events/${eventId}/media-imports/rescan`, { method: "POST" }),
    mediaRescanStatus: (operationId: string, deliveredCount = 0) => client.request<import("./types").MediaRescanProgress>(`/api/v1/admin/media-rescans/${operationId}`, { query: { delivered_count: deliveredCount }, retry: true }),
    retryMedia: (importId: string) => client.request<CentralMediaImport>(
      `/api/v1/admin/media-imports/${importId}/retry`, { method: "POST" },
    ),
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
