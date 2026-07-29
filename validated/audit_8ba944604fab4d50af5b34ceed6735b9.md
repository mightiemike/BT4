### Title
Unbounded `PendingInbounds.Walk` in `afterInboundBallotTerminal` grows linearly with concurrent in-flight inbounds, degrading processing of every ballot-finalizing vote - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
The Sherlock report describes an unbounded loop (`BondAggregator.liveMarketsBy`) that scans an ever-growing set of markets on every call, eventually exceeding the gas budget. The Push Chain analog is `BallotHooks.afterInboundBallotTerminal`, which performs a full `Walk` over the entire `PendingInbounds` collection on **every** inbound-ballot termination to locate the owning entry, because ballot IDs are one-way digests that cannot be mapped back to their `PendingInboundEntry` key directly.

### Finding Description
`x/uexecutor/keeper/ballot_hooks.go` implements `AfterBallotTerminal`, which x/uvalidator invokes synchronously whenever any inbound ballot reaches a terminal state (PASSED/REJECTED/EXPIRED). For `INBOUND_TX` ballots it calls: [1](#0-0) 

This walks **every** currently pending entry in the `PendingInbounds` collection (comment: "the pending set is small and transient") to find the variant whose `BallotId` matches. `PendingInbounds` entries are created the moment the *first* validator vote arrives for a distinct `source_chain:tx_hash:log_index` inbound event, per [2](#0-1) , and are only removed once *all* variants of that entry reach a terminal ballot state (`ballot_hooks.go` lines 113-123).

Since `source_chain`, `tx_hash`, and `log_index` are all derived from attacker-controlled external-chain events, an unprivileged attacker can trivially create many concurrent, distinct pending inbound entries (e.g. many small/cheap deposits on a low-fee source chain, or even invalid/garbage deposits that still register a vote) faster than the validator set can drive them to quorum. Each of these entries stays live in `PendingInbounds` until its own ballot terminates.

`MsgVoteInbound` (the message that triggers this hook) is registered as a **gasless** message type in `app/txpolicy/gasless.go`: [3](#0-2) 

meaning the message bypasses fee deduction but is still executed under the standard SDK gas metering / block gas limit on every honest node. As `n` = number of concurrently pending inbound entries grows (driven purely by attacker-submitted source-chain events), every single terminating vote triggers an `O(n)` full-collection walk, so a burst of `n` inbounds finalizing around the same time produces `O(n²)` aggregate store-iteration work executed inline inside ordinary `MsgVoteInbound` processing on every node — the same growth pattern that caused the referenced BondAggregator bug, just manifesting as processing-cost blow-up instead of an outright `view`-call revert.

### Impact Explanation
This is a liveness/DoS-class issue reachable purely from unprivileged, ordinary cross-chain deposit activity (no malicious validator, node, or privileged actor required): an attacker can inflate the size of `PendingInbounds` by generating a flood of distinct low-cost inbound events on any supported source chain, none of which require validator complicity. Because the resulting `Walk` executes inside the hot path of the gasless `MsgVoteInbound` handler on every node (validators and full nodes alike, since ballot state transitions are consensus state), this degrades block-processing performance for the whole network proportionally to the square of the burst size, rather than being a purely off-chain/network-level DoS. It does not directly move funds, but it threatens the "reachable without privileged control" DoS category called out in scope.

### Likelihood Explanation
Likelihood is moderate: `PendingInbounds` size is naturally self-bounding under healthy conditions (entries are removed as soon as their ballot terminates), so exploitation requires an attacker to sustain a burst of many concurrent, still-unresolved inbound events (e.g. by targeting chains/log-index combinations with slow confirmation or intentionally submitting garbage events that still register at least one vote) faster than the validator set finalizes them. This is achievable by any external party without special privileges, but the achievable `n` is limited by the number of supported source chains and the rate at which validators observe/vote, so the severity scales with sustained attacker effort rather than being instantly catastrophic.

### Recommendation
Avoid the reverse ballot-ID → entry lookup via a full collection scan. Maintain a secondary index (e.g. `ballotID -> utxKey`) that is written alongside `RecordInboundVote`/`VoteOnInboundBallot` and consulted directly in `afterInboundBallotTerminal`, so the hook performs an O(1) lookup instead of an O(n) walk regardless of how many inbounds are concurrently pending.

### Proof of Concept
1. Attacker submits (or causes validators to observe) a large number `n` of distinct, cheap inbound events across supported source chains within a short window — each becomes its own `PendingInboundEntry` via `RecordInboundVote` (`x/uexecutor/keeper/msg_vote_inbound.go`).
2. As validators vote each of these to quorum roughly concurrently, every finalizing `MsgVoteInbound` triggers `afterInboundBallotTerminal`, each performing a full `PendingInbounds.Walk` over the then-current (still large) pending set.
3. Total iteration work across the burst approaches `O(n²)`, executed inline in ordinary gasless message processing on every node, in contrast to the intended "small and transient" assumption documented in the code comment.

### Citations

**File:** x/uexecutor/keeper/ballot_hooks.go (L77-97)
```go
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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L57-66)
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

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
