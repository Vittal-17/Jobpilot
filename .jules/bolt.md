## 2024-05-18 - [Fix O(N log N) file-sort on /jobs endpoint]
**Learning:** When adding multi-column pagination or ordering endpoints, a matching composite index must be created to prevent expensive file-sort operations during full table scans.
**Action:** Created `ix_jobs_pagination` index on `(published_at DESC NULLS LAST, discovered_at DESC, id DESC)` for the `list_jobs` endpoint.
