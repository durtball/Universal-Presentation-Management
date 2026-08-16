import { createCentralClient } from "./client";
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
} from "./types";

let uploadRequestSequence = 0;

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
    participants: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/participants`, signal),
    sessions: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/sessions`, signal),
    presentations: (eventId: string, signal?: AbortSignal) =>
      get<Row[]>(`/api/v1/admin/events/${eventId}/presentations`, signal),
    mediaWorkspace: (eventId: string, signal?: AbortSignal) =>
      get<CentralMediaWorkspace>(`/api/v1/admin/events/${eventId}/media-imports`, signal),
    uploadMedia: (eventId: string, file: File, onProgress: (value: number) => void, relativePath?: string) =>
      xhrUpload<CentralMediaImport>(
        `/api/v1/admin/events/${eventId}/media-imports`, file, onProgress, csrfToken, relativePath,
      ),
    assignMedia: (importId: string, presentationId: string) =>
      client.request<CentralMediaImport>(
        `/api/v1/admin/media-imports/${importId}/assignment/${presentationId}`,
        { method: "PUT" },
      ),
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

function xhrUpload<T>(path: string, file: File, progress: (value: number) => void, csrf: string | null, relativePath?: string) {
  return new Promise<T>((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", path);
    request.responseType = "json";
    request.setRequestHeader("X-UPM-Original-Filename", encodeURIComponent(file.name));
    if (relativePath) request.setRequestHeader("X-UPM-Source-Relative-Path", encodeURIComponent(relativePath));
    request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    request.setRequestHeader("Idempotency-Key", `browser-upload-${Date.now()}-${uploadRequestSequence++}`);
    if (csrf) request.setRequestHeader("X-CSRF-Token", csrf);
    request.upload.onprogress = (event) => progress(event.lengthComputable ? event.loaded / event.total * 100 : 0);
    request.onerror = () => reject(new Error("Upload interrupted. Check the network and retry."));
    request.onload = () => request.status >= 200 && request.status < 300
      ? resolve(request.response as T)
      : reject(new Error(uploadError(request)));
    request.send(file);
  });
}

function uploadError(request: XMLHttpRequest) {
  const detail = request.response?.detail;
  const message = typeof detail === "string" ? detail : detail?.message;
  if (request.status === 401 || request.status === 403) return "Authentication failed. Sign in again and retry.";
  if (request.status === 413) return "File is larger than the configured upload limit.";
  if (request.status === 409) return message || "Duplicate upload or version conflict.";
  return message || `Upload failed${request.status ? ` (HTTP ${request.status})` : ""}.`;
}
