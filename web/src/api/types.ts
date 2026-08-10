export type Row = Record<string, unknown>;

export interface Health {
  service: string;
  status: string;
}
export interface EventRecord extends Row {
  event_id: string;
  name: string;
  description?: string;
  timezone: string;
  starts_at?: string;
  ends_at?: string;
  deployments: Row[];
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
