### Title
Unbounded `PendingInbounds.Walk()` scan in `BallotHooks.afterInboundBallotTerminal` causes DoS scaling with concurrent pending inbounds - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
`x/uexecutor`'s `BallotHooks.afterInboundBallotTerminal` locates the `PendingInbounds` entry that owns a terminating ballot by doing a full, unbounded `k.PendingInbounds.Walk(ctx, nil, ...)` over every live pending-inbound entry in the module, instead of looking the entry up directly by its deterministic `utx_key`. This mirrors the `SurplusGuildMinter`/`ProfitManager.claimRewards()` pattern in the referenced report: work that should be scoped to one specific item (one term / one ballot) is instead performed as a linear scan across an attacker-influenceable, unbounded collection, and this scan executes inside ordinary, unprivileged-triggerable state-machine paths.

### Finding Description
`PendingInbounds` entries are keyed by `utx_key = sha256(source_chain:tx_hash:log_index)` [1](#0-0) , and every unique observed inbound (i.e. every gateway event on any supported external chain) that at least one Universal Validator has voted on gets its own entry/variant in this collection until its ballot(s) reach a terminal state [2](#0-1) .

`BallotHooks.afterInboundBallotTerminal` is invoked by `x/uvalidator`'s generic ballot machine whenever an `INBOUND_TX` ballot reaches a terminal status (`PASSED`/`REJECTED`/`EXPIRED`). To find which `PendingInbounds` entry owns the terminating `ballotID`, it walks the *entire* `PendingInbounds` collection instead of doing a direct `Get` by `utx_key`:

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
``` [3](#0-2) 

The comment justifying this design states "Ballot IDs are one-way canonical digests (not reversible), so locate the owning audit-trail entry by scanning PendingInbounds... The pending set is small and transient" [4](#0-3)  — this is an unverified assumption about bounded size, exactly the kind of assumption the referenced report warns against ("there is no evidence to prove it cannot happen").

This Walk is reachable synchronously inside ordinary `MsgVoteInbound` processing: `VoteInbound` calls `VoteOnInboundBallot`, which drives `uvalidator`'s `VoteOnBallot` → `CheckIfFinalizingVote`; when a ballot's votes make the opposite threshold unreachable it transitions to `REJECTED` and fires the terminal hook [5](#0-4) , [6](#0-5) . Each such vote is a *gasless* transaction (`MsgVoteInbound` is in the gasless whitelist) [7](#0-6) , so the attacker does not even need the honest Universal Validators to pay non-trivial fees to trigger repeated full-collection scans.

Crucially, the size of `PendingInbounds` is driven by external-chain activity that an unprivileged attacker fully controls: anyone can send cheap, distinct gateway transactions on any enabled external chain (distinct `tx_hash`/`log_index` pairs, or slightly different observable field encodings that split UV votes into multiple `InboundVariant`s for the same event [8](#0-7) ). Honest Universal Validators are required to observe and vote on all of them (this is the intended, non-malicious protocol flow), causing many entries with in-flight ballots to co-exist in `PendingInbounds` at once during the window before each individual ballot reaches quorum. Every terminal transition of any one of these ballots then triggers an O(n) scan across *all* other unrelated pending entries — the same "specific-item work degraded into an update-across-everything loop" defect as the `ProfitManager.claimRewards()`/`SurplusGuildMinter.getReward()` bug in the source report, just relocated from an EVM gauge loop to a Cosmos SDK collection Walk inside ballot finalization.

### Impact Explanation
As the number of concurrently pending inbound entries grows (purely from ordinary/cheap external-chain deposit spam, no privileged action required), the per-terminal-ballot cost of `afterInboundBallotTerminal` grows linearly, and the aggregate cost across a burst of terminating ballots grows quadratically. This can materially degrade `MsgVoteInbound` processing latency/gas usage for honest Universal Validators, delaying finalization of legitimate user deposits and, in the worst case, causing per-transaction gas/time blowups analogous to the OOG condition demonstrated in the original report. Because inbound voting and ballot finalization sit on the critical path of universal execution (mint/refund/payload dispatch), this constitutes a reachable, non-network-level denial-of-service against ordinary users' deposit processing.

### Likelihood Explanation
Triggering a large simultaneous population of `PendingInbounds` entries requires no privileged access — only the ability to originate many distinct, cheap transactions on an external chain within a single ballot's voting/expiry window, something any unprivileged actor can do. The design comment's assumption that "the pending set is small and transient" is not enforced anywhere in code (no cap on `PendingInbounds` size, no pagination on the Walk), so likelihood scales directly with how cheap it is to generate distinct external-chain events, similar to how the original finding's likelihood scaled with how many gauges/terms could be added.

### Recommendation
Avoid the full-collection Walk in `afterInboundBallotTerminal`. Maintain a secondary index from `ballotID -> utx_key` (populated in `RecordInboundVote` when a variant/ballot is created) so the hook can do a direct `Get`/lookup instead of scanning every pending entry, mirroring the report's recommendation to scope `ProfitManager.claimRewards` to the single relevant term rather than iterating all gauges.

### Proof of Concept
1. An unprivileged actor sends N distinct cheap transactions to the gateway contract on an enabled external chain (distinct tx hashes / log indices), each recognized as a separate `Inbound` event.
2. Honest Universal Validators observe and begin voting (`MsgVoteInbound`) on all N events roughly concurrently, so all N corresponding ballots are simultaneously `PENDING` in `PendingInbounds` (each occupies its own entry/variant, per `RecordInboundVote`) [9](#0-8) .
3. As soon as any one of these N ballots reaches a terminal state (e.g. a `REJECTED` transition from a validator submitting a differing/failure vote, or crafted variant-splitting inputs), `afterInboundBallotTerminal` executes and walks the full `PendingInbounds` collection (now containing ~N live entries) to find the one matching entry [3](#0-2) .
4. Repeating this for each of the N ballots reaching a terminal state in the same window yields O(N²) total work purely from cheap, unprivileged external-chain spam — exact gas/time thresholds where this becomes consensus-impacting were not measured in this analysis and would need to be benchmarked (e.g. via a load test analogous to the `test_dos` PoC in the source report) to fully quantify severity.

### Citations

**File:** x/uexecutor/keeper/inbound.go (L14-88)
```go
// RecordInboundVote idempotently records a validator's vote on an inbound by
// appending to the per-utx PendingInbounds entry. Creates the entry on the
// first vote for a given utx_key, creates a new variant on the first vote
// of a given (inbound payload bytes / ballotID), and appends the voter to
// an existing variant on subsequent votes for the same payload (deduped).
//
// utx_key = sha256(source_chain:tx_hash:log_index) — see GetInboundUniversalTxKey.
// ballotID = hex(marshal(Inbound)) — see GetInboundBallotKey.
//
// Multiple variants exist for the same utx_key when validators marshal
// slightly different Inbound bytes for the same logical event (different
// decoded fields, formatting, etc.). Each variant tracks which validators
// voted for that exact byte sequence so operators can investigate divergence.
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

	// Find or create the variant for this ballot.
	variantIdx := -1
	for i, v := range entry.Variants {
		if v.BallotId == ballotID {
			variantIdx = i
			break
		}
	}
	if variantIdx < 0 {
		entry.Variants = append(entry.Variants, types.InboundVariant{
			BallotId:           ballotID,
			Inbound:            &inbound,
			Voters:             []string{voter},
			FirstVotedAtHeight: height,
			LastVotedAtHeight:  height,
			TerminalStatus:     uvalidatortypes.BallotStatus_BALLOT_STATUS_PENDING,
		})
	} else {
		v := &entry.Variants[variantIdx]
		// Idempotent voter add.
		already := false
		for _, x := range v.Voters {
			if x == voter {
				already = true
				break
			}
		}
		if !already {
			v.Voters = append(v.Voters, voter)
		}
		v.LastVotedAtHeight = height
	}

	k.Logger().Debug("inbound vote recorded",
		"utx_key", utxKey,
		"ballot_id", ballotID,
		"voter", voter,
		"variant_count", len(entry.Variants),
	)
	return k.PendingInbounds.Set(ctx, utxKey, entry)
}
```

**File:** proto/uexecutor/v1/pending.proto (L49-65)
```text
// PendingInboundEntry tracks all ballot variants for a single logical
// inbound event (identified by utx_key). Created by the first vote
// (RecordInboundVote). Removed only when ALL variants reach a terminal
// state. If any variant ended PASSED, the existing post-finalization
// path produces the UniversalTx. If ALL variants ended EXPIRED/REJECTED,
// the entry is moved to ExpiredInbounds.
message PendingInboundEntry {
  option (gogoproto.equal) = true;

  // sha256(source_chain:tx_hash:log_index) — same key used by
  // GetInboundUniversalTxKey and the UniversalTx record (when it
  // eventually exists).
  string utx_key = 1;
  repeated InboundVariant variants = 2 [(gogoproto.nullable) = false];
  // Block height when this entry was created (first vote on any variant).
  uint64 created_at_height = 3;
}
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L77-80)
```go
	// Ballot IDs are one-way canonical digests (not reversible), so locate
	// the owning audit-trail entry by scanning PendingInbounds for the
	// variant carrying this ballot ID. The pending set is small and
	// transient, and this hook only fires on terminal transitions.
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L86-94)
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
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L60-73)
```go
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}
```

**File:** x/uvalidator/keeper/voting.go (L186-196)
```go
	ballot, err = k.AddVoteToBallot(ctx, ballot, voter, voteResult)
	if err != nil {
		return ballot, false, isNew, err
	}

	ballot, isFinalizing, err := k.CheckIfFinalizingVote(ctx, ballot)
	if err != nil {
		return ballot, false, false, err
	}

	return ballot, isFinalizing, isNew, nil
```

**File:** app/README.md (L163-170)
```markdown
```
/uexecutor.v1.MsgExecutePayload
/uexecutor.v1.MsgVoteInbound
/uexecutor.v1.MsgVoteOutbound
/uexecutor.v1.MsgVoteChainMeta
/utss.v1.MsgVoteTssKeyProcess
/utss.v1.MsgVoteFundMigration
```
```

**File:** x/uexecutor/README.md (L250-253)
```markdown
- **Variant-aware:** when validators marshal slightly different `Inbound` bytes
  for the same logical event (different decoded fields, formatting, etc.), each
  unique payload becomes its own `InboundVariant` inside the entry, with its
  own `ballot_id`, `voters[]`, and `terminal_status`.
```
