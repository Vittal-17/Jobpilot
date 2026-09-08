# Architecture

## Domain Concepts

### Canonical Job Model

The `Job` model is the central domain concept representing a normalized job opportunity.
Regardless of where a job listing is discovered, it is converted into this canonical format before further processing.

Data flow:
```text
FastAPI
   ↓
Pydantic domain model (app.models.job.Job)
   ↓
SQLAlchemy persistence model (app.db.models.job.JobModel)
   ↓
PostgreSQL
```

### Persistence and Domain Separation

The Pydantic domain model and SQLAlchemy persistence model are intentionally kept separate:
1. **Clean Architecture**: The domain layer (Pydantic) has no dependency on the database layer (SQLAlchemy), making it easier to test and reason about.
2. **Validation Rules**: Pydantic handles parsing, structural validation, and ensuring business rules at the application boundary, whereas SQLAlchemy enforces strict database-level constraints (uniqueness, foreign keys, not-null).
3. **Flexibility**: We can change the database schema or ORM without breaking the core domain logic, and vice versa.

### Data Integrity

- **Uniqueness Strategy**: A job is uniquely identified by the tuple `(source, source_job_id)`. We do not assume `source_job_id` is globally unique across different providers, so the database enforces a `UniqueConstraint` on both columns.
- **Constraints**: Database-level `CheckConstraint`s ensure `match_score` is strictly between `0-100` and `salary_min`/`salary_max` are non-negative.

### Multi-Provider Ingestion
The system uses a `JobProvider` interface to normalize external sources (e.g., Adzuna, Jooble) into the canonical `Job` model.
```text
JobSearchQuery
      ↓
Provider Interface
   /     \
Adzuna  Jooble
   \     /
Canonical Job
      ↓
Ingestion Service
      ↓
Job Repository
      ↓
PostgreSQL
```
- **Stable Source IDs**: We extract the actual provider job ID (e.g., Jooble ID, Adzuna ID) as `source_job_id`. This works seamlessly with the `(source, source_job_id)` idempotency constraint in the database, allowing us to safely retry or re-ingest pages without generating duplicate rows.
- **Bangalore-First Query Capability**: The shared `JobSearchQuery` supports a `location` parameter. Providers map this to their respective APIs. Currently, Adzuna yields precise "Bangalore, Karnataka" locations, while Jooble returns broader "India" results, meaning downstream filtering will be necessary to ensure high local relevance.
- **Provider-specific Limitations**: Jooble's API on the `in.jooble.org` domain is actively protected by Cloudflare's IUAM (403 Forbidden), requiring us to route requests through the global `jooble.org` endpoint.
- **Jooble Quota Considerations**: The ingestion service is built defensively. Connection errors, timeouts, or bad statuses skip the batch or single jobs without infinitely retrying.

### Idempotency and Deduplication
- **Provider-Level Idempotency:** Implemented via database unique constraints on `(source, source_job_id)`. If the same job provider returns the exact same job ID in future API calls, PostgreSQL will safely reject the duplicate via `23505 UniqueViolation`, preventing duplicate rows and ensuring safe retries.
- **Semantic Deduplication (Not Implemented):** The system does not yet identify whether "Software Engineer at Google" from Adzuna is the same physical listing as "Software Engineer at Google" from Jooble. Semantic cross-provider deduplication is planned for a future architecture phase.
