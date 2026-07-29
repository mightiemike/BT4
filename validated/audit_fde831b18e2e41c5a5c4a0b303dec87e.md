Confirmed: this is a valid, self-contained analog to M-5.

### Title
Unbounded `PendingInbounds` map linearly scanned on every ballot finalization causes OOG DoS bricking inbound voting - (File: x/uexecutor/keeper/ballot_hooks.go)

### Summary
`x/uexecutor` has no limit on the number of concurrently pending inbound observations tracked in the `PendingInbounds` collection. Each unique source-chain event that receives its first validator vote creates a new entry via `RecordInboundVote` [1](#0-0) . Whenever ANY ballot (inbound) reaches a terminal state (PASSED, REJECTED, or EXPIRED), `AfterBallotTerminal` fires and performs a full, unbounded linear `Walk` over the entire `PendingInbounds` map (plus every variant inside each entry) to locate the entry owning that ballot ID [2](#0-1) . The code comment explicitly (and incorrectly, absent any enforced bound) assumes "the pending set is small and transient" [3](#0-2) .

### Finding Description
The class of bug is identical to Perennial M-5: an unbounded pending-item queue is fully scanned/iterated during ordinary settlement/finalization, with no cap on queue size and no pagination on the scan. In Push Chain's case:

1. An unprivileged attacker can generate many distinct, cheap source-chain inbound events (e.g., dust deposits with distinct `tx_hash`/`log_index` on a low-fee external chain) within a short window.
2. Each such inbound event, once observed and voted on by honest Universal Validators, creates a new `PendingInboundEntry` keyed by `utx_key = sha256(source_chain:tx_hash:log_index)` via `RecordInboundVote`, which is called unconditionally on every first vote for a utx_key — there is no cap on the number of simultaneous entries [4](#0-3) .
3. If enough distinct inbound observations are outstanding at once (validators haven't yet reached quorum on all of them — e.g., due to normal network latency, congestion, or a validator set that only partially votes per block), `PendingInbounds` can grow to a large number of entries while honest validators are still catching up.
4. The moment *any single* inbound ballot (even an unrelated, cheap/dust one) reaches its terminal state via `MarkBallotFinalized`/`MarkBallotExpired`, `fireBallotTerminalHook` invokes `AfterBallotTerminal` → `afterInboundBallotTerminal`, which performs `h.k.PendingInbounds.Walk(ctx, nil, ...)` scanning **every** entry and **every** variant within it to find the one containing the matching `ballot_id` [5](#0-4) .
5. This hook fires synchronously inside the `MsgVoteInbound` message-handling path (`VoteInbound` → `VoteOnInboundBallot` → `CheckIfFinalizingVote` → `MarkBallotFinalized` → `fireBallotTerminalHook`), so the cost of the full-map walk is charged against the gas of whatever validator's vote transaction happens to trigger finalization of any ballot [6](#0-5) .
6. If the number of pending entries × variants grows large enough that the walk exceeds the transaction's gas limit, the vote transaction OOG-reverts. Since the SDK reverts the entire transaction (including the terminal-ballot state transition) atomically on out-of-gas, the ballot and the bloated `PendingInbounds` map remain in the exact same state, so **every subsequent finalizing vote transaction hits the same OOG wall** — this bricks the entire inbound-finalization path for all users protocol-wide, matching M-5's "fully bricks the protocol" impact.

### Impact Explanation
This breaks core protocol functionality: once `PendingInbounds` grows past the point where a single `Walk` fits in a transaction's gas budget, no inbound vote can ever finalize again (whether PASSED, REJECTED, or EXPIRED), since finalizing *any* ballot triggers the same unbounded walk. This halts all further crosschain fund crediting via inbounds — a protocol-wide DoS reachable by ordinary unprivileged users generating many cheap inbound events, without requiring any malicious validator, node, or privileged actor.

### Likelihood Explanation
Likelihood is moderate-to-low but not negligible, similar to the original Perennial finding: it requires a burst of distinct inbound observations to be outstanding simultaneously (validators voting fast enough to observe many events, but slow enough — due to congestion, high external-chain event volume, or partial validator turnout per block — that the outstanding set of pending entries grows large before quorum is reached on each). No malicious validator, admin, or privileged actor is needed; only ordinary users generating many low-cost source-chain events under normal (or degraded) network conditions. The condition worsens as the validator set participates asynchronously or the external chain has high throughput of cheap dust transactions.

### Recommendation
- Track a direct index from `ballot_id → utx_key` (e.g., a `Map[string, string]`) so `afterInboundBallotTerminal` can do an O(1) lookup instead of walking all of `PendingInbounds`.
- Alternatively/additionally, enforce a hard cap on the number of concurrently outstanding `PendingInbounds` entries (and/or variants per entry), rejecting or queuing new `MsgVoteInbound` votes for genuinely new utx_keys once the cap is reached.
- Add gas-based safety: bound the `Walk` with an explicit max-iterations guard and fail closed/log rather than allow unbounded gas consumption inside consensus-critical vote handling.

### Proof of Concept
1. Deploy against a testnet source chain with negligible per-tx fees.
2. Submit a large number (N, sized to exceed a single tx's gas budget once scanned) of distinct dust deposit transactions on the source chain, each with a unique `tx_hash`/`log_index`.
3. Have honest validators observe and vote on all N events in a staggered/asynchronous manner so that at some block height, all N entries are simultaneously present in `PendingInbounds` (none has reached quorum yet).
4. Let quorum be reached on any one of the N ballots. The finalizing `MsgVoteInbound` transaction triggers `afterInboundBallotTerminal`'s `PendingInbounds.Walk` over all N entries.
5. Observe the transaction consumes gas proportional to N × variants-per-entry; with N large enough, the transaction reverts with out-of-gas, and because the revert is atomic, the ballot remains unfinalized and `PendingInbounds` remains bloated — every subsequent finalizing vote transaction reproduces the same OOG failure, demonstrating the persistent DoS.

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

**File:** x/uvalidator/keeper/voting.go (L199-222)
```go
// CheckIfFinalizingVote inspects whether the just-cast vote pushes the ballot
// over its threshold and, if so, drives the finalization through
// MarkBallotFinalized — the single canonical write path for terminal status
// transitions, which applies CEI-style ordering on the secondary indexes.
func (k Keeper) CheckIfFinalizingVote(ctx context.Context, b types.Ballot) (types.Ballot, bool, error) {
	ballot, isFinalizing := b.IsFinalizingVote()
	if !isFinalizing {
		return b, false, nil
	}

	k.Logger().Debug("ballot reached finalization threshold",
		"ballot_id", ballot.Id,
		"ballot_status", ballot.Status.String(),
	)

	if err := k.MarkBallotFinalized(ctx, ballot.Id, ballot.Status); err != nil {
		return ballot, false, errors.Wrap(err, "failed updating finalized ballot")
	}

	k.Logger().Debug("ballot finalized",
		"ballot_id", ballot.Id,
		"ballot_status", ballot.Status.String(),
	)
	return ballot, true, nil
```
