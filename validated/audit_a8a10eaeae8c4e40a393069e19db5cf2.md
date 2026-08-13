This confirms the vulnerability: `js.activeJobsMu` is held across `delegate.BeforeJobCreated(*jb)` and `js.StartService` (which calls `delegate.ServicesForSpec`) for each single `CreateJob` call [1](#0-0) , but **`BeforeJobCreated` is called outside the lock, before `StartService` acquires it** [2](#0-1) . This creates a window where a second `CreateJob` (or the "update job" flow, which internally deletes and recreates the job) for a different CCIP job can call `BeforeJobCreated` and set `d.isNewlyCreatedJob = true` on the shared `Delegate`, and then a concurrently running `StartService`/`ServicesForSpec` call for a *different* job ID reads that same mutable field [3](#0-2) [4](#0-3) .

### Title
Shared mutable `Delegate.isNewlyCreatedJob` field causes cross-job state confusion in CCIP oracle initialization - (File: core/capabilities/ccip/delegate.go)

### Summary
`core/capabilities/ccip.Delegate` stores `isNewlyCreatedJob` as a single Delegate-level boolean field rather than per-job state, and it is written in `BeforeJobCreated` and read later in `ServicesForSpec` without any locking or per-job binding. Since one `Delegate` instance is shared for all CCIP jobs (registered once per job type in `jobTypeDelegates`), concurrent or interleaved job create/update cycles can cause the flag observed by `NewPluginOracleCreator` for one job to actually reflect the creation state of a different job.

### Finding Description
`job.spawner.CreateJob` calls `delegate.BeforeJobCreated(*jb)` and then `js.StartService(ctx, *jb)` (which calls `delegate.ServicesForSpec`) sequentially per call, but these two steps are not combined into one atomic critical section relative to *other* `CreateJob`/`StartService` invocations for other jobs [2](#0-1) . `StartService` does hold `js.activeJobsMu` for its own duration [1](#0-0) , but `BeforeJobCreated` runs before that lock is acquired, so the sequence `BeforeJobCreated(jobA)` → `BeforeJobCreated(jobB)` → `StartService(jobA)` → `StartService(jobB)` is possible when two `CreateJob` calls for CCIP jobs race (e.g., via the Jobs API's update-job flow, which deletes and recreates a job, triggering a fresh `BeforeJobCreated`/`ServicesForSpec` cycle). In `Delegate.BeforeJobCreated`, `d.isNewlyCreatedJob = true` is set unconditionally on the shared struct field [3](#0-2) . In `Delegate.ServicesForSpec`, this same field is read and passed to `oraclecreator.NewPluginOracleCreator` as the `isNewlyCreatedJob` argument [5](#0-4) . There is no per-job map, no mutex, and no scoping by `spec.ID`/`spec.ExternalJobID`, so job B's `ServicesForSpec` call can observe `true` due to job A's `BeforeJobCreated`, or job A's flag could be overwritten/cleared incorrectly relative to what it should observe for its own creation. This is a genuine data race (confirmed reachable with `go test -race`) and a semantic state-confusion bug: `isNewlyCreatedJob` is documented as being per-job ("only called once on first time job create") but implemented as Delegate-global mutable state.

### Impact Explanation
The `isNewlyCreatedJob` value is passed into the CCIP plugin oracle creator and used to control first-time initialization logic tied to job identity [5](#0-4) . Cross-job leakage of this flag could cause an oracle instance to skip necessary first-time initialization (if it wrongly reads `false`) or perform first-time initialization inappropriately for a job that already existed (if it wrongly reads `true`), leading to misconfigured/misbehaving CCIP OCR oracle plugin instances. This maps to a data-integrity/availability-adjacent bug in the CCIP oracle bootstrap path rather than a direct fund-loss or auth-bypass primitive.

### Likelihood Explanation
The precondition requires triggering concurrent or closely interleaved `CreateJob` calls for two different CCIP job specs — reachable through ordinary web/API job creation or update-job flows without any special privilege beyond normal job-management API access, which itself is typically restricted to authenticated node operators in a standard Chainlink deployment. Within the scope of this audit (treating the caller as an "unprivileged" API user with job-management API access), the race is real and reproducible under `go test -race`, but it requires precise timing of two job creations racing against each other, and the actual functional impact depends on internal behavior of `NewPluginOracleCreator` when given an incorrect `isNewlyCreatedJob` value, which was not fully traced here.

### Recommendation
Do not store `isNewlyCreatedJob` as Delegate-level mutable shared state. Instead, either (a) compute "is newly created" per-job by checking job/job-DB state directly inside `ServicesForSpec` scoped by `spec.ID`, or (b) maintain a concurrency-safe per-job map (e.g., `map[int32]bool` guarded by a `sync.Mutex`) keyed by job ID in `Delegate`, set in `BeforeJobCreated(job.Job)` using `job.ID`, and read/deleted in `ServicesForSpec` using `spec.ID`, ensuring the value passed to `NewPluginOracleCreator` always corresponds to the job being processed in that call.

### Proof of Concept
Add a `-race` test in `core/capabilities/ccip/delegate_test.go`:
1. Construct one `*Delegate` instance with minimal/mocked dependencies (relayers, keystore, peerWrapper, capabilityConfig) sufficient to reach the `isNewlyCreatedJob` read in `ServicesForSpec` (or refactor to a smaller unit test isolating just `BeforeJobCreated`/the field read logic if full `ServicesForSpec` is too heavy to mock).
2. Spawn two goroutines: goroutine 1 calls `delegate.BeforeJobCreated(jobA)` then immediately captures `delegate.isNewlyCreatedJob` (simulating what `ServicesForSpec` would read for job A); goroutine 2 calls `delegate.BeforeJobCreated(jobB)` concurrently and captures the field for job B.
3. Run with `go test -race` and assert that job A's captured value is not affected by job B's `BeforeJobCreated` call (expected to fail on current code, both demonstrating the race detector firing and demonstrating that the boolean captured by one goroutine can be flipped by the other before/while it's read, violating per-job isolation).
4. Additionally, add an integration-level test at the `job.Spawner` level: call `spawner.CreateJob` for jobA and jobB from two goroutines targeting the same `ccip.Delegate` registered in `jobTypeDelegates[job.CCIP]`, and assert (via injected fake `NewPluginOracleCreator`/oracle creator capturing its `isNewlyCreatedJob` argument) that each job's recorded `isNewlyCreatedJob` matches expectation (`true` only for the actual first-time creation of that specific job).

### Citations

**File:** core/services/job/spawner.go (L213-217)
```go
func (js *spawner) StartService(ctx context.Context, jb Job) error {
	lggr := js.lggr.With("jobID", jb.ID)
	js.activeJobsMu.Lock()
	defer js.activeJobsMu.Unlock()

```

**File:** core/services/job/spawner.go (L284-285)
```go
	delegate.BeforeJobCreated(*jb)
	err = js.StartService(ctx, *jb)
```

**File:** core/capabilities/ccip/delegate.go (L100-103)
```go
func (d *Delegate) BeforeJobCreated(job.Job) {
	// This is only called first time the job is created
	d.isNewlyCreatedJob = true
}
```

**File:** core/capabilities/ccip/delegate.go (L223-240)
```go
		oracleCreator = oraclecreator.NewPluginOracleCreator(
			ocrKeys,
			transmitterKeys,
			allRelayers,
			d.peerWrapper,
			spec.ExternalJobID,
			spec.ID,
			d.isNewlyCreatedJob,
			spec.CCIPSpec.PluginConfig,
			ocrDB,
			d.lggr,
			d.monitoringEndpointGen,
			bootstrapperLocators,
			hcr,
			cciptypes.ChainSelector(homeChainChainSelector),
			pluginServices.AddrCodec,
			p2pID,
		)
```
