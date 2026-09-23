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
