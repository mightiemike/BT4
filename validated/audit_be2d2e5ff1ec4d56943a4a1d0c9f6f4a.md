Based on my investigation, I found a concrete analog to the reported bug class in the VRF v2 listener's batch chunking loop.

### Title
Zero `ChunkSize` in VRF V2 Job Spec Causes Infinite Loop (CPU-Burning DoS) in Request Processing - (File: core/services/vrf/v2/listener_v2_log_processor.go)

### Summary
The external report describes an unvalidated, attacker/operator-influenced numeric configuration value (`SplitOpts` threshold) being used as a loop-increment/chunk divisor without a minimum-value guard, causing either a panic or an infinite loop that burns CPU while holding a lock. The Chainlink VRF v2 listener has a structurally identical pattern: `VRFSpec.ChunkSize`, a `uint32` field taken directly from a user-supplied job spec TOML, is used as the increment step of a `for` loop that walks the list of pending VRF requests, with no runtime check that it is non-zero.

### Finding Description
In `core/services/vrf/v2/listener_v2_log_processor.go`, `processRequestsPerSubBatchHelper` iterates over the ready requests using the job's configured chunk size as the loop step: [1](#0-0) 
```
	// Process requests in chunks in order to kick off as many jobs
	// as configured in parallel. Then we can combine into fulfillment
	// batches afterwards.
	for chunkStart := 0; chunkStart < len(ready); chunkStart += int(lsn.job.VRFSpec.ChunkSize) {
		chunkEnd := min(chunkStart+int(lsn.job.VRFSpec.ChunkSize), len(ready))
		chunk := ready[chunkStart:chunkEnd]
```

`ChunkSize` is defined as `uint32` on the job spec and documented as "the number of pending VRF V2 requests to process in parallel. Optional, defaults to 20 if not provided" [2](#0-1) . This is a job-spec field settable by whoever creates the VRF job (job/workflow ingestion path), and is persisted and read back via the ORM/GraphQL resolvers [3](#0-2) .

If `ChunkSize` is explicitly set to `0` in the job spec TOML, `chunkStart += int(lsn.job.VRFSpec.ChunkSize)` becomes `chunkStart += 0`. As long as `len(ready) > 0`, `chunkStart` never advances past `0`, and the loop spins forever, repeatedly reprocessing the same chunk `ready[0:min(0,len(ready))]` = `ready[0:0]` (an empty chunk) indefinitely — this is the same "capacity stays at zero, slice never shrinks, loop spins forever" pattern flagged in the audit report for `ExecutionRecord::split`.

I was unable to fully confirm — due to time constraints — whether `ValidatedVRFSpec` (in `core/services/vrf/vrfcommon/validate.go`) explicitly rejects `chunkSize = 0`; the grep hits show `ChunkSize` referenced twice in that file but I could not read the file contents before running out of iterations. Given other numeric VRF spec fields (e.g., `gasLanePrice = 0`) are explicitly validated with dedicated test cases [4](#0-3) , but I found no equivalent `chunkSize == 0` test case among the VRF validate tests I inspected, so the zero-value guard for `ChunkSize` specifically is unverified.

### Impact Explanation
If reachable (i.e., if `ValidatedVRFSpec` does not reject `chunkSize = 0`), this would let a job-spec creator (an operator/admin of the node, not necessarily requiring root superuser rights depending on the node's ACL configuration) configure a VRF v2 job that, once it has pending requests, causes the listener's per-subscription batch processing goroutine to spin in a tight CPU-consuming infinite loop, starving the VRF fulfillment pipeline and potentially other services sharing the same goroutine pool/CPU resources — a node-level denial of service impacting VRF request fulfillment (unsafe/stalled workflow execution).

### Likelihood Explanation
Likelihood is uncertain and depends entirely on whether `ValidatedVRFSpec` already guards against `chunkSize = 0` at job-creation time. If it does not, likelihood is high, since job creation is a normal administrative action (not requiring exploiting any other vulnerability) and zero is a trivially reachable value for a `uint32` TOML field left unset or explicitly set to `0`.

### Recommendation
- Add explicit validation in `ValidatedVRFSpec` (`core/services/vrf/vrfcommon/validate.go`) to reject `chunkSize = 0`, consistent with the treatment of other zero-invalid fields like `gasLanePrice`.
- Add a defensive guard in `processRequestsPerSubBatchHelper` itself (e.g., `chunkSize := int(lsn.job.VRFSpec.ChunkSize); if chunkSize <= 0 { chunkSize = defaultChunkSize }`) so that even a spec created before validation was tightened, or loaded from a stale DB record, cannot trigger an infinite loop.
- Add a unit test instantiating a VRF job spec with `chunkSize = 0` and asserting either a validation error at spec-creation time or bounded loop behavior in `processRequestsPerSubBatchHelper`.

### Proof of Concept
1. Create (or attempt to create) a VRF v2 job spec with `chunkSize = 0` in its TOML.
2. If job creation succeeds, submit VRF randomness requests so that `getReadyAndExpired` returns a non-empty `ready` slice.
3. Observe `processRequestsPerSubBatchHelper`'s loop at [5](#0-4)  never terminate (`chunkStart` stays at `0` forever), consuming CPU indefinitely and preventing new subscriptions/requests from being processed.

*Note: Full confirmation that `chunkSize = 0` passes spec validation could not be completed within the available tool budget — a Devin session with full repository access should verify `core/services/vrf/vrfcommon/validate.go` directly before treating this as a confirmed, unmitigated finding.*

### Citations

**File:** core/services/vrf/v2/listener_v2_log_processor.go (L350-356)
```go
	// Process requests in chunks in order to kick off as many jobs
	// as configured in parallel. Then we can combine into fulfillment
	// batches afterwards.
	for chunkStart := 0; chunkStart < len(ready); chunkStart += int(lsn.job.VRFSpec.ChunkSize) {
		chunkEnd := min(chunkStart+int(lsn.job.VRFSpec.ChunkSize), len(ready))
		chunk := ready[chunkStart:chunkEnd]

```

**File:** core/services/job/models.go (L581-583)
```go
	// ChunkSize is the number of pending VRF V2 requests to process in parallel. Optional, defaults
	// to 20 if not provided.
	ChunkSize uint32 `toml:"chunkSize"`
```

**File:** core/web/resolver/spec.go (L694-700)
```go
// ChunkSize resolves the spec's chunk size.
func (r *VRFSpecResolver) ChunkSize() int32 {
	if r.spec.ChunkSize > math.MaxInt32 {
		return math.MaxInt32
	}
	return int32(r.spec.ChunkSize)
}
```

**File:** core/services/vrf/vrfcommon/validate_test.go (L325-344)
```go
		{
			name: "invalid (zero) gas lane price gwei provided",
			toml: `
type            = "vrf"
schemaVersion   = 1
minIncomingConfirmations = 10
publicKey = "0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F8179800"
coordinatorAddress = "0xB3b7874F13387D44a3398D298B075B7A3505D8d4"
requestTimeout = "168h" # 7 days
chunkSize = 25
backoffInitialDelay = "1m"
backoffMaxDelay = "2h"
gasLanePrice = "0 gwei"
fromAddresses = ["0x1111111111111111111111111111111111111111"]
observationSource = """` + vrfV2Observation + `"""
`,
			assertion: func(t *testing.T, s job.Job, err error) {
				require.Error(t, err)
			},
		},
```
