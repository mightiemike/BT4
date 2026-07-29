### Title
Unbounded `PendingInbounds.Walk` full-collection scan on every inbound ballot terminal transition enables attacker-inflated DoS of inbound vote processing - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
`x/uexecutor`'s `BallotHooks.AfterBallotTerminal` → `afterInboundBallotTerminal` performs a full linear `Walk` over the entire `PendingInbounds` collection every time *any* inbound ballot reaches a terminal state (`EXPIRED`, `PASSED`, `REJECTED`). This fires on the routine, honest-validator-driven `MsgVoteInbound` path whenever quorum (or expiry) is reached for *any* single inbound. The size of `PendingInbounds` is controlled by an unprivileged attacker who can cheaply generate many distinct crosschain deposit events on an external chain, each of which becomes its own `PendingInboundEntry` once observed and voted on by Universal Validators. This is the direct structural analog of the Canto `setPeriodSize`/`allPairs` unbounded-loop finding: an attacker-inflatable array is walked in full on a normal, non-paginated operation that everyone depends on.

### Finding Description
`PendingInbounds` is a `collections.Map[string, types.PendingInboundEntry]` keyed by `utx_key = sha256(source_chain:tx_hash:log_index)`. An entry is created the moment the *first* validator votes an inbound observation via `MsgVoteInbound` (`RecordInboundVote`), and is only removed once *all* ballot variants for that key reach a terminal state [1](#0-0) .

Because Push Chain is a permissionless bridge hub, an unprivileged attacker fully controls how many distinct inbound events exist to be voted on: they can submit many small/dust deposits (or even payload transactions that revert cheaply) on any connected external chain, each producing a distinct gateway log (`source_chain:tx_hash:log_index` tuple), causing Universal Validators to open a new `PendingInboundEntry` for each one [2](#0-1) .

On every single terminal transition of an inbound ballot (which happens continuously and routinely as normal traffic finalizes or expires — not gated by admin or rare operations), `x/uvalidator`'s `MarkBallotFinalized`/`MarkBallotExpired` invoke the registered `BallotHooks.AfterBallotTerminal` callback [3](#0-2) [4](#0-3) .

For `INBOUND_TX` ballots, this dispatches to `afterInboundBallotTerminal`, which — because ballot IDs are one-way digests and not reversible — resorts to scanning **the entire `PendingInbounds` collection** to find the entry whose variant carries the matching `ballotID`:

```go
err := h.k.PendingInbounds.Walk(ctx, nil, func(key string, e types.PendingInboundEntry) (bool, error) {
    for _, v := range e.Variants {
        if v.BallotId == ballotID {
            utxKey, entry, found = key, e, true
            return true, nil
        }
    }
    return false, nil
})
``` [5](#0-4) 

The code comment explicitly rationalizes this as acceptable only because "the pending set is small and transient" [6](#0-5)  — an assumption that does not hold under the exact bug class described in the external report: an attacker can keep the pending set large by continuously generating new inbounds faster than they can naturally terminate, or simply by having many inbounds in flight concurrently before any of them expire (`DefaultExpiryAfterBlocks` gives each ballot a window to remain pending) [7](#0-6) .

This is triggered from `VoteInbound` on the standard, gasless `MsgVoteInbound` path — every single validator vote that causes a ballot to finalize walks the whole map, so the cost of processing *any one* inbound vote scales with the *total* number of concurrently pending inbounds across the whole chain, a quantity an unprivileged party can inflate at will.

### Impact Explanation
As the number of concurrently pending inbounds grows (attacker-controlled, cheap to produce via many small external-chain deposits/log events), the linear `Walk` cost incurred on *every* ballot termination grows proportionally. Because `MsgVoteInbound` is a gasless message type (per `x/uexecutor/README.md`, bonded UV-only but gasless) [8](#0-7) , there is no fee-based backpressure discouraging spam of the underlying source-chain events that create these entries, and no fee-scaling mechanism protects validators' vote transactions from ballooning gas/compute cost as the pending set grows. In the worst case this degrades to the same class of impact as the Canto finding: honest validator vote transactions for *unrelated* inbounds become progressively more expensive/slow to process (in-process compute, not user-paid gas, since these are Cosmos SDK collection walks executed by validator nodes), and if left unbounded could push inbound-vote processing time or resource usage high enough to materially delay finalization of legitimate crosschain deposits network-wide — a functional denial-of-service on the universal execution pipeline, reachable purely by unprivileged externally-triggered deposit spam, not by any privileged or malicious-validator assumption.

### Likelihood Explanation
Likelihood is moderate: exploitation requires no privileged access — only the ability to originate many distinct gateway events on a supported external chain (dust deposits are typically cheap on most EVM/SVM testnets/L2s, and Push Chain aims to support many external chains with varying gas costs). The attacker does not need to defeat any voting/ballot invariant; they only need `PendingInbounds` to be populated faster or held open longer than it drains, which is achievable by submitting more inbound events than validators can finalize within one `DefaultExpiryAfterBlocks` window, or simply submitting a very large batch prior to a wave of terminal transitions. The `x/uexecutor/README.md` and code comments show developers were aware the set is intended to be "small and transient" but did not add a hard cap or alternate index keyed by ballot ID to avoid the linear scan, indicating this was not defended against.

### Recommendation
- Maintain a secondary index (e.g. `collections.Map[ballotID, utxKey]`) so `afterInboundBallotTerminal` can do an O(1) lookup instead of a full `Walk` over `PendingInbounds`.
- Alternatively, cap the maximum number of concurrently-pending distinct inbound entries (or per-source-chain rate-limit inbound observation admission) so the walked set has a bounded worst-case size.
- Add metrics/alerting on `PendingInbounds` size so operators can detect anomalous growth before it degrades vote-processing throughput.

### Proof of Concept
1. An unprivileged actor submits `N` distinct low-value deposit transactions (or gateway-emitting payload calls) on a connected external chain in rapid succession, each with a unique `tx_hash`/`log_index`.
2. Universal Validators observe and vote each one via `MsgVoteInbound`; each unique inbound creates a new `PendingInboundEntry` in `PendingInbounds` (per `RecordInboundVote`) before any of them terminate, growing the collection to size `N`.
3. As soon as any one ballot (attacker's or an unrelated legitimate user's, doesn't matter which) reaches quorum or expires, `VoteInbound` → `uvalidatorKeeper.VoteOnBallot` → `MarkBallotFinalized`/`MarkBallotExpired` fires `BallotHooks.AfterBallotTerminal` → `afterInboundBallotTerminal`, which performs a `Walk` over all `N` entries in `PendingInbounds` [9](#0-8) .
4. Repeating step 1 continuously (refilling the pending set as entries terminate) keeps `N` large indefinitely, so every subsequent unrelated inbound-vote transaction pays the O(N) scan cost, degrading throughput of the entire inbound-finalization pipeline for all users — mirroring the Canto `setPeriodSize`/`allPairs` unbounded-loop DoS pattern where an unprivileged party inflates an array that a routine, non-paginated operation must fully scan.

**Note on verification limits:** I was unable to confirm within available tool calls whether there is any existing hard cap on concurrently pending inbounds, the exact `DefaultExpiryAfterBlocks` value, or per-source-chain rate limits in `x/uexecutor/types/constants.go` / `msg_vote_inbound.go` (the final grep for these returned only file names, not contents, before iterations were exhausted). If such a cap exists and is tight enough to bound `PendingInbounds` to a small constant size in practice, the severity of this finding would be substantially reduced. A Devin session with full read access should verify these constants before treating this as confirmed-exploitable at scale.

### Citations

**File:** x/uexecutor/README.md (L199-205)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |
```

**File:** x/uexecutor/README.md (L244-260)
```markdown
### `PendingInbounds`

- **Created** by the FIRST validator vote on a given inbound (`RecordInboundVote`
  inside `VoteInbound`). The chain learns about the source-chain event from
  validator observations.
- **Keyed** by `utx_key = sha256(source_chain:tx_hash:log_index)`.
- **Variant-aware:** when validators marshal slightly different `Inbound` bytes
  for the same logical event (different decoded fields, formatting, etc.), each
  unique payload becomes its own `InboundVariant` inside the entry, with its
  own `ballot_id`, `voters[]`, and `terminal_status`.
- **Removed** when ALL related ballot variants reach a terminal state. If any
  variant ended `PASSED`, the existing post-finalization path in `VoteInbound`
  produced a `UniversalTx`. If ALL variants ended `EXPIRED`/`REJECTED`, the
  full per-variant audit trail is moved to `ExpiredInbounds` for the future
  escape-hatch refund flow.
- The cleanup-on-terminal logic lives in `keeper/ballot_hooks.go` (the
  `BallotHooks` impl wired into `x/uvalidator`).
```

**File:** x/uvalidator/keeper/ballot.go (L182-188)
```go
	ballot.Status = status
	if err := k.Ballots.Set(ctx, id, ballot); err != nil {
		return err
	}

	k.fireBallotTerminalHook(ctx, ballot.Id, ballot.BallotType, status)
	return nil
```

**File:** x/uvalidator/keeper/ballot.go (L191-212)
```go
// fireBallotTerminalHook invokes the registered BallotHooks (if any) and
// log-swallows any error. Terminal transitions must never be blocked by
// secondary-index side-effect failure.
func (k Keeper) fireBallotTerminalHook(
	ctx context.Context,
	ballotID string,
	ballotType types.BallotObservationType,
	status types.BallotStatus,
) {
	if k.ballotHooks == nil {
		return
	}
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := k.ballotHooks.AfterBallotTerminal(sdkCtx, ballotID, ballotType, status); err != nil {
		k.Logger().Warn("ballot terminal hook returned error",
			"ballot_id", ballotID,
			"ballot_type", ballotType.String(),
			"status", status.String(),
			"err", err.Error(),
		)
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

**File:** x/uexecutor/keeper/voting.go (L122-123)
```go
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
```
