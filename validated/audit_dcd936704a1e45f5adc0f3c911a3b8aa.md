Important: `CreateBallot` (x/uvalidator/keeper/ballot.go:36-39) calls `ExpireBallotsBeforeHeight` on **every single new ballot creation** — i.e. on every distinct inbound observation submitted via `MsgVoteInbound`. `ExpireBallotsBeforeHeight` (x/uvalidator/keeper/ballot.go:320-364) walks the entire `ActiveBallotIDs` set every time. Combined with `afterInboundBallotTerminal`'s full `PendingInbounds.Walk` (x/uexecutor/keeper/ballot_hooks.go:86-94) firing on every terminal ballot, both scans are O(n) over attacker-inflatable state and execute synchronously inside `DeliverTx`/`FinalizeBlock`, i.e. inside the deterministic state machine, not off-chain.

### Title
Unbounded Ballot/PendingInbounds Backlog Enables Compute-Amplification DoS via Cheap External-Chain Spam - (File: `x/uvalidator/keeper/ballot.go`, `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
Push Chain's ballot machine creates one `Ballot` per distinct inbound observation and tracks per-utx-key audit trails in `PendingInbounds`. Two hot paths perform full linear scans over this attacker-inflatable state on every single vote: `CreateBallot` calls `ExpireBallotsBeforeHeight`, which walks all of `ActiveBallotIDs` [1](#0-0) , and `afterInboundBallotTerminal` walks all of `PendingInbounds` looking for the variant matching a given ballot ID [2](#0-1) . This is the direct on-chain analog of the reported Compound `allMarkets`/`claimComp()` out-of-gas pattern: an unbounded list, iterated on a hot path, whose size is influenced by unprivileged, low-cost external activity.

### Finding Description
An inbound `Ballot` and a `PendingInbounds` entry are created the moment ANY validator votes on ANY inbound observation via `MsgVoteInbound` [3](#0-2) . `MsgVoteInbound` is a gasless message, restricted only to bonded Universal Validators as the *signer*, but the *content being voted on* — the source-chain event itself — is fully attacker-controlled: any unprivileged party can send arbitrarily many cheap/dust transactions to the gateway contract on an external chain (e.g. a low-fee EVM chain or Solana), each producing a distinct `utx_key = sha256(source_chain:tx_hash:log_index)`. Honest Universal Validators are expected to observe and vote on these events as part of normal protocol operation (see `pollOutboundEvents`/inbound analog in `universalClient/chains/push/event_listener.go`), so this requires no malicious validator — only an attacker willing to pay trivial external-chain gas.

Each such vote:
1. Calls `CreateBallot` if the ballot doesn't already exist, which unconditionally invokes `ExpireBallotsBeforeHeight`, iterating the entire `ActiveBallotIDs` collection [4](#0-3) .
2. Calls `RecordInboundVote`, appending a new `PendingInboundEntry`/`InboundVariant` keyed by the new `utx_key` [5](#0-4) .
3. On terminal transition (pass/reject/expire), `afterInboundBallotTerminal` performs a full `PendingInbounds.Walk` to find the entry owning the ballot ID, because ballot IDs are one-way digests and not directly reversible to a `utx_key` [2](#0-1) .

The code comments in `ballot_hooks.go` explicitly assume "the pending set is small and transient" — an assumption not enforced anywhere in code. Nothing caps the number of concurrently-pending inbound ballots/entries, and nothing throttles or rate-limits how many distinct source-chain events an attacker can generate. As the backlog `n` grows, each new vote and each terminal transition costs `O(n)`, so processing `n` attacker-seeded garbage inbounds costs `O(n²)` aggregate CPU inside `FinalizeBlock`/`DeliverTx` — the deterministic consensus execution path, not an off-chain/network-level concern.

### Impact Explanation
This is a denial-of-service vector reachable by an unprivileged external actor without needing any malicious validator, admin, or key compromise — it fits the in-scope "denial of service ... not network-level ... reachable without privileged control" criterion. If the backlog grows large enough (attacker floods a cheap external chain's gateway with thousands of tiny/garbage deposit-shaped events), per-vote and per-terminal-transition processing time increases proportionally, slowing block production for the whole network and potentially causing legitimate inbound/outbound vote processing to stall or blocks to approach timeout — degrading availability of the universal execution pipeline for all users. Because `MsgVoteInbound` is gasless, validators incur no fee cost for casting the flood of votes either, removing an economic backpressure that would otherwise exist on a fee-metered message type.

### Likelihood Explanation
Moderate-to-high. The attack requires no privileged access — only the ability to submit cheap transactions to an external chain gateway that honest, protocol-compliant Universal Validators will faithfully observe and vote on. The two O(n) scans (`ExpireBallotsBeforeHeight` over `ActiveBallotIDs`, and `PendingInbounds.Walk` in `afterInboundBallotTerminal`) both execute unconditionally on the hot vote path, and there is no cap, pagination, or backlog-size guard in either. The severity scales with how cheaply an attacker can generate distinct source-chain events (log_index variation alone is sufficient to generate distinct `utx_key`s from a single transaction with multiple events), making the practical cost of inflating `n` very low on some supported chains.

### Recommendation
Following the audit report's own recommendation for the analogous Compound issue: perform gas/compute simulations to determine a safe maximum number of concurrently pending inbound ballots/entries, and enforce it. Concretely:
- Replace the `PendingInbounds.Walk` reverse-lookup in `afterInboundBallotTerminal` with a proper secondary index (e.g. `ballot_id -> utx_key`) so terminal-transition handling is O(1) instead of O(n).
- Cap the number of concurrently pending inbound ballots (or entries per source chain), rejecting/backpressuring new inbound votes once the cap is hit, with expiry-driven eviction prioritized.
- Consider skipping the unconditional `ExpireBallotsBeforeHeight` walk on every `CreateBallot` call in favor of a periodic (e.g. `EndBlocker`) sweep bounded by a fixed batch size per block, decoupling ballot expiry cost from per-vote cost.

### Proof of Concept
1. Attacker submits `N` (e.g. 50,000) trivial/dust transactions to the gateway contract on a cheap external chain (varying `log_index` per tx to cheaply multiply distinct `utx_key`s).
2. Honest Universal Validators observe each event as part of normal operation and submit `MsgVoteInbound` for each — gasless, so no fee friction on the validator side.
3. Each `MsgVoteInbound` triggers `CreateBallot` → `ExpireBallotsBeforeHeight` (full `ActiveBallotIDs` scan) and `RecordInboundVote` (append to `PendingInbounds`).
4. As votes accumulate toward 2/3 quorum or expiry, each ballot's terminal transition triggers a full `PendingInbounds.Walk` over the now-large backlog in `afterInboundBallotTerminal`.
5. Measure `FinalizeBlock`/`DeliverTx` latency for `MsgVoteInbound` processing as `N` grows; demonstrate superlinear growth in per-vote and per-terminal-transition latency, causing observable block-time degradation network-wide.

### Citations

**File:** x/uvalidator/keeper/ballot.go (L36-39)
```go
	// First, expire any old ballots before this height
	if err := k.ExpireBallotsBeforeHeight(ctx, blockHeight); err != nil {
		return types.Ballot{}, err
	}
```

**File:** x/uvalidator/keeper/ballot.go (L320-347)
```go
// ExpireBallotsBeforeHeight checks active ballots and marks expired ones.
// It uses a two-phase approach: first collect IDs to expire, then mutate,
// to avoid modifying the ActiveBallotIDs collection during iteration.
func (k Keeper) ExpireBallotsBeforeHeight(ctx context.Context, currentHeight int64) error {
	iter, err := k.ActiveBallotIDs.Iterate(ctx, nil)
	if err != nil {
		return err
	}

	// Phase 1: collect IDs to expire
	var toExpire []string
	for ; iter.Valid(); iter.Next() {
		id, err := iter.Key()
		if err != nil {
			iter.Close()
			return err
		}

		ballot, err := k.Ballots.Get(ctx, id)
		if err != nil {
			iter.Close()
			return err
		}

		if ballot.BlockHeightExpiry <= currentHeight {
			toExpire = append(toExpire, id)
		}
	}
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L77-94)
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
```

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
