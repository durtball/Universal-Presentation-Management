import { siteClient } from "./client";
import { uploadFile } from "./upload";
import type {
  Health,
  OperationsDashboard,
  ProgramLocation,
  Row,
  SiteDeployment,
  SiteDevice,
  SiteMedia,
  SiteRegistration,
  RoomDetail,
  SiteRoom,
  StorageTarget,
  SiteMediaWorkspace,
  PresentationMediaVersion,
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
  room: (roomId: string, signal?: AbortSignal) =>
    get<RoomDetail>(`/api/v1/rooms/${roomId}`, signal),
  createRoom: (label: string) =>
    siteClient.request<SiteRoom>("/api/v1/rooms", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    }),
  updateRoom: (
    roomId: string,
    update: { label?: string; enabled?: boolean; archived?: boolean; revision?: number },
  ) =>
    siteClient.request<RoomDetail>(`/api/v1/rooms/${roomId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    }),
  programLocations: (eventId: string, signal?: AbortSignal) =>
    get<ProgramLocation[]>(
      `/api/v1/events/${eventId}/program-room-locations`,
      signal,
    ),
  mapProgramLocation: (eventId: string, importedLabel: string, roomId: string | null) =>
    siteClient.request<ProgramLocation>(
      `/api/v1/events/${eventId}/program-room-mappings`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ imported_label: importedLabel, room_id: roomId }),
      },
    ),
  devices: (signal?: AbortSignal) => get<SiteDevice[]>("/api/v1/devices", signal),
  assignDevice: (roomId: string, role: "primary" | "backup", deviceId: string | null) =>
    siteClient.request<RoomDetail>(
      `/api/v1/rooms/${roomId}/device-assignments/${role}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId }),
      },
    ),
  media: (signal?: AbortSignal) => get<SiteMedia[]>("/api/v1/media", signal),
  mediaWorkspace: (eventId: string, signal?: AbortSignal) =>
    get<SiteMediaWorkspace>(`/api/v1/events/${eventId}/media`, signal),
  matchMedia: (eventId: string, filename: string, signal?: AbortSignal) =>
    siteClient.request<Row>(`/api/v1/events/${eventId}/media/match`, {
      signal, retry: true, query: { filename },
    }),
  createVersion: (presentationId: string) =>
    siteClient.request<PresentationMediaVersion>(`/api/v1/presentations/${presentationId}/versions`, { method: "POST" }),
  uploadMedia: (
    siteId: string, eventId: string, file: File, versionId: string | null,
    onProgress: (value: number) => void, relativePath?: string, onRetry?: (count: number) => void,
  ) => uploadFile<SiteMedia>({ path: "/api/v1/media/ingestions", file, progress: onProgress, relativePath, retrying: onRetry, query: {
      site_id: siteId, event_id: eventId,
      category: versionId ? "presentation_version" : "open_file",
      presentation_version_id: versionId ?? undefined,
      expected_size: file.size,
    } }),
  retryReplication: (replicationId: string) =>
    siteClient.request<Row>(`/api/v1/media-replications/${replicationId}/retry`, { method: "POST" }),
  operations: (signal?: AbortSignal) =>
    get<OperationsDashboard>("/api/v1/operations/dashboard", signal),
  retrySync: () =>
    siteClient.request<Row>("/api/v1/sync/retry-failed", { method: "POST" }),
};
