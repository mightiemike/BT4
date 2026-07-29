### Title
Unbounded `PendingInbounds.Walk` in ballot-terminal hook allows attacker-inflated pending set to degrade honest-validator inbound-vote processing - ([File: x/uexecutor/keeper/ballot_hooks.go])

### Summary
`afterInboundBallotTerminal` performs a full `Walk` over the entire `PendingInbounds` collection on every single inbound ballot terminal transition (`PASSED`/`EXPIRED`/`REJECTED`), searching linearly for the entry that owns a given `ballotID`. The comment explicitly assumes "the pending set is small and transient," but nothing in the reachable code enforces that assumption — an unprivileged attacker can inflate the number of concurrently-pending `PendingInboundEntry` records by generating many distinct source-chain inbound events (analogous to the "large `positionIdList`" pattern in the reference report: an attacker-inflated, unbounded per-key list that is iterated in full on every state-transition of a shared, size-sensitive resource).

### Finding Description
`VoteInbound` ( [1](#0-0) ) calls `RecordInboundVote`, which creates/updates a `PendingInboundEntry` keyed by `utx_key = sha256(source_chain:tx_hash:log_index)` for every distinct inbound observation. Because each `utx_key` is derived from attacker-controlled/attacker-triggerable source-chain data (tx hash, log index, sender, chain), an attacker can trivially create many distinct pending entries simply by causing (or performing) many distinct low-value/low-cost source-chain transactions that honest Universal Validators will observe and vote on — this requires no privileged role, only ordinary use of the supported source chains.

When any one ballot among these many pending entries reaches a terminal status, `x/uvalidator` invokes `BallotHooks.AfterBallotTerminal` → `afterInboundBallotTerminal`, which does: [2](#0-1) 

This `Walk` scans **every** entry in `PendingInbounds` and, for each entry, iterates its `Variants` slice looking for the terminal `ballotID`. The code comment acknowledges the design assumption but does not enforce any cap: [3](#0-2) 

If an attacker keeps `N` inbound events pending simultaneously (e.g., by using multiple source chains, or timing dust transactions so many events are in flight before quorum/expiry), each of the `N` terminal events triggers an O(N) walk, yielding O(N²) total work concentrated in the block range where these events resolve (ballot expiry windows tend to line up because they use the same expiry parameter). This is executed synchronously inside the `MsgVoteInbound` message handler for **every terminal transition**, i.e., inside the transaction submitted by an honest validator — so the attacker does not even need to pay for the walk; honest validators' vote transactions absorb the cost.

### Impact Explanation
This is a denial-of-service vector against the honest-validator inbound voting/finalization path, not a network-level or privileged-actor attack — it is reachable purely by an external attacker generating ordinary (if numerous) source-chain transactions that validators are expected to observe and vote on. As `N` grows, the per-vote cost of `afterInboundBallotTerminal` grows linearly and the aggregate cost during a terminal-transition burst grows quadratically, which can materially slow down or stall processing of `MsgVoteInbound`/`MsgVoteOutbound`-adjacent finalization for the whole system, delaying legitimate users' inbound funds/payload execution and outbound processing during the period the pending set is inflated. This mirrors the reference bug class: an attacker-grown, unbounded per-resource list that is fully scanned on a critical state-transition path (there: liquidation; here: ballot finalization/pending-inbound bookkeeping), causing degraded availability rather than a hard invariant break (no funds are stolen or double-spent by this path alone).

### Likelihood Explanation
Moderate. Exploitation requires the attacker to generate a large number of distinct inbound events across supported source chains within a bounded time window (so they remain concurrently pending), which costs the attacker real (if potentially small) gas/fees on those external chains, and does not directly break custody invariants — it degrades throughput/latency of validator vote processing. There is no cap on `PendingInbounds` size enforced anywhere in the reachable code, and the walk is unconditionally triggered on every terminal ballot regardless of collection size, so the vulnerability is straightforward to trigger, but its severity scales with the attacker's willingness/ability to sustain a large number of concurrent low-cost cross-chain transactions.

### Recommendation
Avoid the full-collection scan to resolve `ballotID → PendingInboundEntry`. Maintain a secondary index (`ballotID -> utx_key`) that is written alongside `RecordInboundVote`/variant creation, so `afterInboundBallotTerminal` can do an O(1) lookup instead of an O(n) `Walk`. Additionally, consider bounding the number of concurrently pending inbound entries (or ballot-expiry-window overlap) to eliminate the underlying unbounded-collection assumption entirely, consistent with how the original Panoptic fix recommended capping the unbounded list rather than relying on it staying "small."

### Proof of Concept
Conceptual (not executed, as this requires a live multi-validator devnet across several source chains to demonstrate timing):
1. Attacker submits `K` distinct, low-value transactions on one or more supported source chains (distinct `tx_hash`/`log_index` pairs), causing honest UVs to submit `MsgVoteInbound` for each, each creating a `PendingInboundEntry` (`RecordInboundVote` in `x/uexecutor/keeper/msg_vote_inbound.go`).
2. Attacker times these so that most of the `K` entries are concurrently pending (e.g., they share similar ballot-expiry windows).
3. As ballots begin reaching terminal state (pass/expire), each terminal event triggers `afterInboundBallotTerminal`, which performs a full `PendingInbounds.Walk` over the current pending set (`x/uexecutor/keeper/ballot_hooks.go:86-94`).
4. As `K` scales up, the cumulative work done inside honest validators' `MsgVoteInbound` transactions during the terminal-resolution window grows quadratically, measurably slowing down inbound finalization throughput for all users during that period.

Note: I was unable to fully verify, from the indexed code alone, the exact `BallotExpiryBlocks` value or whether there is any existing cap on concurrent inbound observations per source chain that would bound `K` in practice — a full engagement with the running node/devnet would be needed to quantify the achievable `K` and the resulting wall-clock degradation precisely.

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L56-66)
```go

	// Step 2: Record this validator's vote in the per-utx PendingInbounds entry
	// (variant-aware audit trail). Each unique Inbound payload becomes its own
	// variant; multiple variants per utx_key indicate validator divergence.
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L76-97)
```go
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
