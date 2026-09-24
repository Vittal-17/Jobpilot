export interface JobResponse {
  id: number;
  title: string;
  company: string;
  source: string;
  location?: string | null;
  remote?: boolean | null;
  employment_type?: string | null;
  description?: string | null;
  description_is_snippet: boolean;
  salary_min?: number | null;
  salary_max?: number | null;
  currency?: string | null;
  url?: string | null;
  published_at?: string | null;
  discovered_at: string;
}

export interface MatchReason {
  code: string;
  message: string;
}

export interface PaginatedJobResponse {
  items: JobResponse[];
  total: number;
  page: number;
  size: number;
}

export interface MatchResult {
  job_id: number;
  score: number | null;
  reasons: MatchReason[] | null;
}

export interface RecommendedJobResponse {
  job: JobResponse;
  match: MatchResult | null;
  recommended_at: string;
  delivery_status?: string;
}

export interface PaginatedRecommendedJobResponse {
  items: RecommendedJobResponse[];
  total: number;
  page: number;
  size: number;
}

export interface SystemStatusResponse {
  engine_active: boolean;
  last_sync: string | null;
  latest_execution_status: string | null;
  total_processed: number;
}

export interface UserResponse {
  id: number;
  email: string;
  is_active: boolean;
  created_at: string;
  last_login_at?: string | null;
}

export interface ProfileResponse {
  headline?: string | null;
  location?: string | null;
  experience_years?: number | null;
  skills?: string | null;
  preferred_roles?: string | null;
  preferred_locations?: string | null;
  remote_preference?: string | null;
}

export interface ProfileUpdate {
  headline?: string | null;
  location?: string | null;
  experience_years?: number | null;
  skills?: string | null;
  preferred_roles?: string | null;
  preferred_locations?: string | null;
  remote_preference?: string | null;
}

export type ApplicationStatus = 'applied' | 'interviewing' | 'rejected' | 'offer' | 'withdrawn';

export interface ApplicationResponse {
  id: number;
  status: ApplicationStatus;
  created_at: string;
  updated_at: string;
  job: JobResponse;
}

export interface ApplicationUpdate {
  status: ApplicationStatus;
}

export interface PaginatedApplicationResponse {
  items: ApplicationResponse[];
  total: number;
  page: number;
  size: number;
}

export interface SavedJobResponse {
  id: number;
  saved_at: string;
  job: JobResponse;
}

export interface PaginatedSavedJobResponse {
  items: SavedJobResponse[];
  total: number;
  page: number;
  size: number;
}

export interface UserSearchResponse {
  id: number;
  query?: string | null;
  location?: string | null;
  remote_only: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PaginatedUserSearchResponse {
  items: UserSearchResponse[];
  total: number;
  page: number;
  size: number;
}

export interface UserSearchCreate {
  query?: string | null;
  location?: string | null;
  remote_only?: boolean;
  enabled?: boolean;
}

export interface UserSearchUpdate {
  query?: string | null;
  location?: string | null;
  remote_only?: boolean | null;
  enabled?: boolean | null;
}
