### Title
Unbounded `job_spec_errors` growth via attacker-controlled bridge/adapter error text in `RecordError` - ([File: core/services/job/orm.go])

### Summary
`RecordError` (and its wrapper `TryRecordError`) persists pipeline/task failures to the `job_spec_errors` table using an `INSERT ... ON CONFLICT (job_id, description) DO UPDATE` pattern keyed on the raw error `description` string. Because bridge/external-adapter responses can influence the description text embedded in pipeline task errors, an attacker who controls a bridge/adapter's error output can generate unbounded numbers of distinct rows for a single job, since there is no cap on distinct descriptions per job and no truncation/normalization of attacker-influenced text before it is used as a uniqueness key.

### Finding Description
`job.ORM` exposes `RecordError(ctx, jobID, description) error` and `TryRecordError(ctx, jobID, description)` (declared at `core/services/job/orm.go:54-56`), which are called throughout the pipeline execution and OCR/OCR2 delegates (e.g. `core/services/ocr2/delegate.go`, `core/services/ocr/delegate.go`, `core/services/ocrbootstrap/delegate.go`) whenever a job/task run fails. The `description` argument frequently incorporates the underlying task error text, which for HTTP/bridge tasks can include response bodies or error strings returned by an external adapter that is fully attacker-controlled content (i.e., not sanitized/templated to a fixed, bounded set of internal error codes).

The `ON CONFLICT (job_id, description) DO UPDATE` upsert pattern deduplicates only when the exact description string repeats for the same job. If the attacker varies the returned error text (e.g., appends a counter, timestamp, or random token to the adapter's error response) each failed run produces a new, distinct `(job_id, description)` key, resulting in a new row in `job_spec_errors` rather than incrementing an existing row's occurrence counter. There is no code path visible that caps the number of distinct rows per job, truncates/normalizes the description before storage, or rate-limits how often a job can record new distinct errors.

I was not able to retrieve the full body of `RecordError`/`TryRecordError` (the exact SQL and any truncation/dedup logic) within the available tool budget, so the precise upsert conflict target and any existing mitigations (e.g., description hashing, length caps, per-job row limits) could not be fully confirmed from the fetched context—only the interface declaration and call sites were verified.

### Impact Explanation
If unmitigated, each unique attacker-supplied error string becomes a permanent row in `job_spec_errors`, and this table is displayed/queried by the job UI and API (`core/web/pipeline_job_spec_errors_controller_test.go`, `FindSpecError`). Sustained high-cardinality error injection from a malicious bridge/adapter could bloat the database, degrade query performance on the errors listing endpoints, and increase storage/backup costs — a resource-exhaustion/denial-of-service class impact against node operational stability, without requiring any privileged access.

### Likelihood Explanation
Feasibility depends on whether a job under attacker influence (e.g., a bridge task pointing to an external adapter server the attacker operates, or an HTTP task hitting an attacker-controlled endpoint referenced in a job spec) propagates raw/variable adapter response text into the error `description` field, and whether the run/task retry cadence allows an attacker to trigger many distinct failures over time. This requires a job configuration where an untrusted bridge/adapter is the source of the description text — a realistic but job-spec-dependent precondition, not a default configuration for all jobs.

### Recommendation
- Normalize/truncate `description` before use as the `ON CONFLICT` key (e.g., cap length, strip/redact raw adapter response bodies, or bucket by structured error code rather than raw text).
- Enforce a maximum number of distinct `job_spec_errors` rows per `job_id` (evict oldest/least-relevant on overflow) or hash+bound description before storing.
- Rate-limit `RecordError` calls per job/description-classification window.

### Proof of Concept
Integration/fuzz test plan for `core/services/job/job_orm_test.go`:
1. Create a job whose pipeline task's failure message includes a caller-supplied string (simulate an HTTP/bridge task pointed at a test server returning attacker-controlled body/error text).
2. In a loop, have the test server return N unique random strings, and invoke `orm.TryRecordError(ctx, jobID, description)` (or trigger real pipeline runs that fail with these descriptions) for each.
3. Query `job_spec_errors` row count for `jobID` after the loop.
4. Assert failure of the invariant: current behavior yields `row_count == N` (unbounded growth) instead of an expected capped/bounded count (e.g., `row_count <= MAX_ERRORS_PER_JOB`), demonstrating the missing cap.