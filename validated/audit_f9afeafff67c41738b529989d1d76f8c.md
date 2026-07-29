### Title
Unbounded `PendingInbounds` growth via attacker-triggered external-chain events forces an O(n) full-map `Walk` on every inbound ballot finalization - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
`RecordInboundVote` creates a new `PendingInboundEntry` keyed by `sha256(source_chain:tx_hash:log_index)` every time Universal Validators observe and vote on a *new* source-chain gateway event [1](#0-0) . Because honest, unprivileged validators vote on every observed gateway event regardless of value, an attacker who submits many cheap/low-value transactions to the external-chain gateway can force the creation of arbitrarily many `PendingInboundEntry` records. On every single inbound ballot's terminal transition, `BallotHooks.afterInboundBallotTerminal` performs a full, unbounded `h.k.PendingInbounds.Walk(ctx, nil, ...)` over *all* entries to find the one owning the terminating ballot ID, explicitly justified by the comment "the pending set is small and transient" [2](#0-1) . This is the same bug class as the reported `MFDBase` issue: an unprivileged actor can flood a shared/queued data structure that a critical, frequently-invoked code path later iterates in full.

### Finding Description
- `PendingInbounds` is a `collections.Map[string, types.PendingInboundEntry]`, unbounded in size and only removed on terminal ballot transitions [3](#0-2) .
- Entries are created the moment the *first* UV votes an inbound (`RecordInboundVote` inside `VoteInbound`), which happens for **every** distinct `(source_chain, tx_hash, log_index)` tuple UVs observe on the gateway — there is no minimum-value or rate-limiting gate before an entry is created [1](#0-0) , and the module README confirms creation happens purely "by the FIRST validator vote" [4](#0-3) .
- `afterInboundBallotTerminal` is the `BallotHooks` implementation invoked by `x/uvalidator`'s generic ballot machine every time *any* inbound ballot reaches a terminal state (PASSED/REJECTED/EXPIRED) — including ballots unrelated to the attacker's flood. It walks the **entire** `PendingInbounds` map linearly to find the variant matching the terminating `ballotID`, with no size bound or index shortcut [5](#0-4) .
- Since an attacker only needs to trigger cheap, legitimate-looking gateway events on an external chain (which UVs are designed to observe permissionlessly and without value filtering), the size of `PendingInbounds` is effectively attacker-controlled, and the cost of the `Walk` scales linearly with that attacker-controlled size. Every honest node executing block logic pays this cost on every terminal ballot transition, for as long as the flood entries remain pending (which, per the README, can persist until ballot expiry).

This mirrors the reported analog precisely: `MFDBase.stake()` lets an unprivileged party grow another entity's array cheaply, and a later, frequently-called function iterates that array in full with no bound, causing gas/resource costs to scale with attacker input. Here, the "array" is the shared `PendingInbounds` collection and the "later function" is `afterInboundBallotTerminal`, invoked on the hot path of every inbound ballot finalization.

### Impact Explanation
This is a network-wide computational/resource-exhaustion concern rather than a single-user fund lock: as `PendingInbounds` grows unboundedly from attacker-triggered external-chain spam, the per-terminal-ballot cost of `afterInboundBallotTerminal`'s `Walk` grows linearly, increasing block-processing time and node resource consumption for **every** validator processing inbound ballots — not just the attacker's own inbounds. In the worst case this could materially slow inbound processing (any inbound ballot's finalization pays the flood tax) or contribute to block-time degradation, degrading availability of the inbound-execution path for all users, reachable purely through ordinary, unprivileged external-chain transaction submission (no validator or admin privilege required).

### Likelihood Explanation
Feasibility is bounded mainly by the cost of generating many distinct gateway events on the configured external chain(s) (i.e., paying source-chain gas for many small/cheap transactions), which is realistic for a moderately funded attacker, especially on low-fee EVM/SVM chains that Push Chain integrates with. The comment "the pending set is small and transient" in the code shows the design explicitly assumes bounded size and does not defend against this growth path, making the likelihood of the O(n) walk becoming meaningfully expensive plausible once an attacker sustains the flood, though I was not able to fully verify (within available tooling) exactly how fast honest UVs would push flooded inbounds to ballot expiry/removal, nor precisely quantify at what map size the `Walk` cost becomes materially disruptive to block processing — that would require load-testing or deeper review of `x/uvalidator`'s ballot expiry cadence and the underlying KV store's iteration cost characteristics.

### Recommendation
Avoid the full-collection `Walk` in `afterInboundBallotTerminal`. Maintain a secondary index/reverse-lookup from `ballotID -> utxKey` (set alongside `PendingInbounds.Set` in `RecordInboundVote`) so the hook can do a direct O(1) lookup instead of scanning every pending entry. Additionally, consider bounding/rate-limiting how many concurrent `PendingInboundEntry` records a single source chain (or source-chain sender) can have outstanding, and/or enforcing a minimum bridged value or per-source-chain throttle before a `PendingInboundEntry` is created, so that the size of `PendingInbounds` cannot be inflated purely by attacker-controlled cheap external-chain spam.

### Proof of Concept
Conceptual PoC (not runnable without a live external-chain fixture and full integration harness):
1. Attacker deploys/uses a source chain registered in `x/uregistry` (e.g., `eip155:11155111`) and submits N (e.g., thousands) of minimal-value transactions to the chain's gateway contract, each producing a distinct `(source_chain, tx_hash, log_index)` triple.
2. Honest Universal Validators observe and vote each one via `MsgVoteInbound`, causing `RecordInboundVote` to create N `PendingInboundEntry` records in the shared `PendingInbounds` map [6](#0-5) .
3. Any subsequent inbound ballot (from any user's legitimate inbound, or one of the attacker's own) reaching a terminal state invokes `afterInboundBallotTerminal`, which performs `h.k.PendingInbounds.Walk(ctx, nil, ...)` over all N entries to find the matching ballot ID [7](#0-6) .
4. As N grows, the per-ballot-termination cost grows linearly, imposing an attacker-controlled, unbounded per-block processing cost on every node — the intended existing-code assumption ("the pending set is small and transient") is violated.

This would need to be validated with an integration test analogous to `test/integration/uexecutor/pending_inbound_audit_trail_test.go`, scaled to a large N of concurrent pending inbounds, to empirically measure the `Walk` cost and confirm materially degraded ballot-finalization latency — this step was not performed here due to tool/time constraints.

### Citations

**File:** x/uexecutor/keeper/inbound.go (L27-46)
```go
func (k Keeper) RecordInboundVote(
	ctx context.Context,
	inbound types.Inbound,
	voter string,
	ballotID string,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	height := uint64(sdkCtx.BlockHeight())
	utxKey := types.GetInboundUniversalTxKey(inbound)

	entry, err := k.PendingInbounds.Get(ctx, utxKey)
	if err != nil && !errors.Is(err, collections.ErrNotFound) {
		return err
	}
	if errors.Is(err, collections.ErrNotFound) {
		entry = types.PendingInboundEntry{
			UtxKey:          utxKey,
			CreatedAtHeight: height,
		}
	}
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L72-97)
```go
func (h BallotHooks) afterInboundBallotTerminal(
	ctx context.Context,
	ballotID string,
	status uvalidatortypes.BallotStatus,
) error {
	// Ballot IDs are one-way canonical digests (not reversible), so locate
	// the owning audit-trail entry by scanning PendingInbounds for the
	// variant carrying this ballot ID. The pending set is small and
	// transient, and this hook only fires on terminal transitions.
	var (
		utxKey string
		entry  types.PendingInboundEntry
		found  bool
	)
	err := h.k.PendingInbounds.Walk(ctx, nil, func(key string, e types.PendingInboundEntry) (bool, error) {
		for _, v := range e.Variants {
			if v.BallotId == ballotID {
				utxKey, entry, found = key, e, true
				return true, nil
			}
		}
		return false, nil
	})
	if err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/keeper.go (L38-43)
```go
	// PendingInbounds tracks in-flight inbounds with full per-variant
	// audit trail (which validators voted what payload, terminal status
	// per variant). Created on first vote (RecordInboundVote), removed
	// when all variants reach a terminal state (BallotHooks impl).
	// See plan-pending-inbound-cleanup.md.
	PendingInbounds collections.Map[string, types.PendingInboundEntry]
```

**File:** x/uexecutor/README.md (L244-248)
```markdown
### `PendingInbounds`

- **Created** by the FIRST validator vote on a given inbound (`RecordInboundVote`
  inside `VoteInbound`). The chain learns about the source-chain event from
  validator observations.
```
