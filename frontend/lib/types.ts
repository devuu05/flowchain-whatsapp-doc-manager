// Mirrors backend/app/schemas/api.py. Secrets are never part of these types.

export type Role = "owner" | "admin" | "member";

export interface Membership {
  tenant_id: string;
  tenant_name: string;
  role: Role;
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
  csrf_token: string;
  memberships: Membership[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: string;
  timezone: string;
  ai_enabled: boolean;
  ai_consent_note: string | null;
  max_file_bytes: number | null;
  monthly_document_quota: number;
  monthly_ai_quota: number;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  status: string;
  original_filename: string;
  standardized_filename: string | null;
  extension: string;
  mime_type: string;
  size_bytes: number;
  document_type: string | null;
  company_or_person: string | null;
  date_or_period: string | null;
  doc_date: string | null;
  folder: string | null;
  review_required: boolean;
  confidence: number | null;
  sender: string | null;
  created_at: string;
  stored_at: string | null;
  failure_reason: string | null;
}

export interface Job {
  id: string;
  kind: string;
  status: string;
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
  error_category: string | null;
  document_id: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface DocumentDetail extends DocumentItem {
  description: string | null;
  summary: string | null;
  classifier: string | null;
  extraction_status: string | null;
  extraction_warnings: string[];
  page_count: number | null;
  char_count: number | null;
  storage_provider: string | null;
  storage_file_id: string | null;
  sha256: string;
  jobs: Job[];
}

export interface AuditEvent {
  id: string;
  actor_type: string;
  actor_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  ip: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Overview {
  documents_total: number;
  documents_this_month: number;
  stored: number;
  failed: number;
  awaiting_description: number;
  review_required: number;
  jobs_queued: number;
  jobs_dead: number;
  by_type: Record<string, number>;
  recent_documents: DocumentItem[];
  recent_activity: AuditEvent[];
}

export interface Usage {
  month_start: string;
  documents_this_month: number;
  monthly_document_quota: number;
  ai_classifications_this_month: number;
  monthly_ai_quota: number;
  messages_sent_this_month: number;
  storage_bytes_this_month: number;
  transient_blob_bytes: number;
  ai_enabled: boolean;
  ai_provider_configured: boolean;
}

export interface Integration {
  id: string;
  kind: "whatsapp" | "google_drive" | "onedrive";
  provider: string;
  name: string;
  status: string;
  settings: Record<string, unknown>;
  last_error: string | null;
  last_checked_at: string | null;
  created_at: string;
  has_secrets: boolean;
  webhook_url: string | null;
}

export interface Member {
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
}

export type SenderRole = "member" | "admin";

export interface Sender {
  id: string;
  phone: string;
  display_name: string;
  is_active: boolean;
  role: SenderRole;
  created_at: string;
}

export interface Category {
  id: string;
  name: string;
  folder_name: string;
  is_active: boolean;
  ai_allowed: boolean;
  keywords: string[];
}
