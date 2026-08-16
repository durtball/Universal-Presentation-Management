export type Row = Record<string, unknown>;

export interface Health {
  service: string;
  status: string;
}
export interface AuthSession {
  authenticated: true;
  user: {
    user_id: string;
    username: string;
    display_name: string;
    roles: string[];
  };
  expires_at: string;
  csrf_token?: string;
}
export interface EventRecord extends Row {
  event_id: string;
  name: string;
  description?: string;
  timezone: string;
  starts_at?: string;
  ends_at?: string;
  deployments: EventDeployment[];
}
export interface EventWrite {
  name: string;
  timezone: string;
  starts_at: string | null;
  ends_at: string | null;
}
export interface EventDeployment extends Row {
  deployment_id: string; event_id: string; site_id: string; site_name: string;
  status: string; synchronization_state: string; desired_revision: number;
  applied_revision: number; update_available: boolean; failure_reason?: string | null;
  successfully_deployed_at?: string | null; summary_counts: Row;
}
export interface EventDeploymentPreview extends Row {
  event_id: string; event_name: string; site_id: string; site_name: string;
  counts: { rooms:number; sessions:number; presenters:number; presentations:number };
  warnings: Array<{code:string;message:string}>;
  errors: Array<{code:string;message:string}>;
  deployable: boolean; existing_deployment_id?: string | null; next_revision:number;
}
export interface SiteRecord extends Row {
  site_id: string;
  display_name: string;
  enrollment_state: string;
  connectivity: string;
  last_seen_at?: string;
  last_successful_sync_at?: string;
  health?: Row;
  pending_sync: number;
  failed_sync: number;
}
export interface PersonRecord extends Row {
  person_id: string;
  display_name: string;
  primary_email?: string;
  organization?: string;
  professional_title?: string;
}
export interface DeletionPreview extends Row {
  target_id?: string;
  confirmation: string;
  impact: Record<string, number>;
}
export interface DeletionOperation extends Row {
  deletion_operation_id: string;
  target_type: "event" | "person" | "people_bulk";
  target_display_name: string;
  status: string;
  stage: string;
  dependency_counts: Record<string, number>;
  site_statuses: Array<{ site_id: string; display_name: string; status: string }>;
  last_error?: string | null;
}
export type ImportStatus =
  | "uploaded"
  | "parsing"
  | "staged"
  | "review"
  | "ready"
  | "committing"
  | "committed"
  | "failed"
  | "cancelled";
export interface ImportBatch extends Row {
  import_batch_id: string;
  event_id: string;
  filename: string;
  status: ImportStatus;
  row_count: number;
  valid_count: number;
  warning_count: number;
  conflict_count: number;
  committed_count: number;
  rejected_count: number;
  failure_summary?: string | null;
  created_at: string;
  committed_at?: string | null;
  source_headers?: string[];
  detected_mapping?: Record<string, string>;
  sample_rows?: Row[];
  preview_counts?: Record<string, number>;
  rows?: ImportRow[];
}
export interface ImportRow extends Row {
  import_row_id: string;
  source_row_number: number;
  raw_values: Row;
  normalized_values: Row;
  entity_type: string;
  validation_state: string;
  match_outcome?: string;
  proposed_person_id?: string;
  candidate_person_ids: string[];
  match_reason?: string;
  conflict_state?: string;
  resolution_action?: string;
  committed_entity_ids: Row;
  issues: Array<{ severity: string; code: string; field_name?: string; message: string }>;
}
export interface SiteRegistration extends Row {
  site_id: string;
  display_name: string;
  registration_state: string;
  connection_status: string;
  last_successful_heartbeat?: string;
  last_successful_sync?: string;
  pending_outbound: number;
  failed_sync: number;
  protocol_compatible: boolean;
  last_error?: string;
}
export interface StorageTarget extends Row {
  storage_target_id: string;
  display_name: string;
  available: boolean;
  writable: boolean;
  health: string;
  free_bytes?: number;
  total_bytes?: number;
  detail?: string;
}
export interface SiteDeployment extends Row {
  deployment_id: string;
  central_event_id: string;
  event_name?: string;
  status: string;
  desired_revision: number;
  applied_revision: number;
  central_connected: boolean;
  summary_counts?: Row;
  failure_reason?: string;
}
export interface RoomMapping extends Row {
  imported_label: string;
  normalized_imported_label: string;
  mapping_status: "mapped" | "unmapped" | "conflict";
  target_room_id?: string;
  target_room_label?: string;
}
export interface SiteRoom extends Row {
  room_id: string;
  site_id: string;
  event_id?: string;
  label: string;
  enabled: boolean;
  archived: boolean;
  archived_at?: string | null;
  revision: number;
  endpoints: Record<string, RoomEndpoint>;
  summary: RoomSummary;
}
export interface RoomEndpoint extends Row {
  device_id: string;
  name: string;
  role?: string;
  status: string;
  online?: boolean | null;
  last_heartbeat?: string | null;
  ip_address?: string | null;
  interface?: string | null;
  version?: string | null;
  telemetry_available: boolean;
}
export interface RoomSummary extends Row {
  health: string;
  session_count: number;
  presentation_count: number;
  ready_count: number;
  missing_count: number;
  error_count: number;
  processing_count: number;
  transfer_pending_count: number;
  next_session?: { session_id: string; title: string; starts_at: string } | null;
}
export interface RoomDetail extends SiteRoom {
  program_mappings: ProgramLocation[];
  sessions: RoomSession[];
}
export interface ProgramLocation extends Row {
  event_id: string;
  imported_label: string;
  normalized_imported_label: string;
  program_room_mapping_id?: string | null;
  mapping_status: "mapped" | "unmapped" | "conflict";
  mapping_source?: "site" | "deployment" | null;
  session_count: number;
  room?: { room_id: string; label: string; enabled: boolean; archived: boolean } | null;
}
export interface RoomPresentation extends Row {
  presentation_id: string;
  title: string;
  presentation_code?: string;
  scheduled_at?: string;
  workflow_status: string;
  processing_status: string;
  operational_status: string;
  media: SiteMedia[];
}
export interface RoomSession extends Row {
  session_id: string;
  event_id: string;
  title: string;
  starts_at?: string;
  ends_at?: string;
  location_name?: string;
  status: string;
  presenters: Array<{ name: string; role: string }>;
  presentations: RoomPresentation[];
}
export interface SiteDevice extends RoomEndpoint {
  site_id: string;
  assignable: boolean;
  assigned_room_id?: string | null;
}
export interface SiteMedia extends Row {
  media_object_id: string;
  file?: string;
  filename?: string;
  original_filename?: string;
  source_relative_path?: string | null;
  presentation?: { presentation_id: string; title: string } | null;
  version_number?: number | null;
  size_bytes?: number | null;
  mime_type?: string | null;
  category?: string;
  availability: string;
  processing_state?: string | null;
  processing_error?: string | null;
  ingested_at: string;
  checksum?: string | null;
  hash_algorithm?: string | null;
  transfer_state?: string | null;
}
export interface OperationsDashboard extends Row {
  rooms: SiteRoom[];
  attention: Array<{
    severity: string;
    kind: string;
    room_id?: string;
    room_label?: string;
    count?: number;
    message: string;
  }>;
  upcoming_sessions: Array<{
    session_id: string;
    title: string;
    starts_at: string;
    room_id: string;
    room_label: string;
  }>;
  failed_processing_jobs: number;
  failed_transfer_jobs: number;
}

export interface MediaReplication extends Row {
  replication_session_id: string;
  state: string;
  confirmed_offset: number;
  expected_size: number;
  retry_count: number;
  last_progress_at?: string | null;
  last_error?: string | null;
  job_status?: string | null;
}
export interface PresentationMediaVersion extends Row {
  presentation_version_id: string;
  version_number: number;
  sync_state?: string;
  media?: {
    media_object_id: string;
    original_filename: string;
    canonical_filename?: string | null;
    size_bytes?: number | null;
    sha256?: string | null;
    availability: string;
    failure_reason?: string | null;
  } | null;
  replication?: MediaReplication | null;
}
export interface PresentationMediaRow extends Row {
  presentation_id: string;
  presentation_identifier?: string | null;
  title: string;
  scheduled_at?: string | null;
  media_state: string;
  sync_state?: string;
  presenters?: string;
  session?: string;
  room?: string;
  versions: PresentationMediaVersion[];
}
export interface SiteMediaWorkspace extends Row {
  summary: { expected: number; missing: number; ready: number; sync_pending: number };
  presentations: PresentationMediaRow[];
}
export interface CentralMediaImport extends Row {
  media_import_id: string;
  event_id: string;
  destination_site_id?: string | null;
  presentation_id?: string | null;
  presentation_version_id?: string | null;
  presentation_identifier?: string | null;
  original_filename: string;
  source_relative_path?: string | null;
  canonical_filename?: string | null;
  size_bytes?: number | null;
  mime_type?: string | null;
  sha256?: string | null;
  match_state: string;
  match_reason?: string | null;
  match_candidates: string[];
  import_state: string;
  sync_state: string;
  origin: string;
  retry_count: number;
  error_code?: string | null;
  error_detail?: string | null;
  created_at: string;
  updated_at: string;
}
export interface CentralMediaWorkspace extends Row {
  summary: Record<string, number>;
  imports: CentralMediaImport[];
}
