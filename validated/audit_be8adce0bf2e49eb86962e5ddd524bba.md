### Title
Unbounded per-ballot full-scan of `ActiveBallotIDs` in `CreateBallot` enables a gasless, attacker-driven DoS of ballot finalization - (File: `x/uvalidator/keeper/ballot.go`)

### Summary
This is a native analog of the Y2K `rolloverQueue` bug: a single shared, unbounded collection (`ActiveBallotIDs`) is fully iterated on every processing round, and an unprivileged user can cheaply force new "rounds" to happen, driving the per-operation cost of the shared queue toward O(N) — and the cumulative cost of an attack toward O(N²) — with no economic disincentive because the triggering messages are gasless.

### Finding Description
`CreateBallot` unconditionally calls `ExpireBallotsBeforeHeight` before persisting a brand-new ballot: [1](#0-0) 

`ExpireBallotsBeforeHeight` walks the **entire** `ActiveBallotIDs` collection and does a `Ballots.Get` for every entry to check expiry, before any mutation is allowed: [2](#0-1) 

`CreateBallot` is reached from `GetOrCreateBallot`, which is invoked from `VoteOnBallot` — the single, generic entry point used by every crosschain observation type (`VoteInbound`, `VoteOutbound`, `VoteChainMeta`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration`): [3](#0-2) 

A ballot is created lazily the first time any validator votes on a **new, distinct** observation ID (a new inbound UTX key/ballot hash, a new outbound ID, etc.). All of these vote messages are in the gasless allowlist, so the Cosmos fee for submitting them is zero: [4](#0-3) 

An unprivileged user can cheaply generate an unbounded stream of *distinct* observations without any minimum-value or rate-limit gate analogous to Y2K's `minRequiredDeposit`. The clearest low-cost vector is repeatedly calling `MsgExecutePayload` (any account may submit it, gasless on the Cosmos side) with payloads that make the UEA emit a `UniversalTxOutbound` event each time; each such event produces a distinct, deterministic `outbound_id` and creates a new `PendingOutbounds` entry with no minimum-amount check: [5](#0-4) 

When honest UVs later vote on each of these outbounds via `MsgVoteOutbound`, each distinct outbound produces a *new* ballot ID, hitting `CreateBallot` → full `ActiveBallotIDs` scan. As the attacker keeps producing new distinct outbounds faster than existing ballots finalize/expire, `ActiveBallotIDs` grows without bound, and **every** subsequent ballot creation — including ballots for completely unrelated, legitimate inbound/outbound/chain-meta/TSS votes from honest validators — pays the cost of scanning the now-large active set. This directly mirrors the Y2K pattern: a shared, unbounded queue that must be fully walked on every "round," populated for near-zero cost by an unprivileged actor, degrading service for all future rounds.

### Impact Explanation
Because the triggering messages are gasless, the attacker pays only Push Chain EVM gas from their own UEA balance (cheap, self-controlled, no external-chain fee, no minimum-value gate on outbound creation) to generate new ballot IDs, while the cost of `ExpireBallotsBeforeHeight`'s full scan is paid by every validator executing every subsequent vote transaction in consensus. This is not network-level DoS — it is a state-machine cost-amplification DoS reachable purely through the default `MsgExecutePayload` submission path plus the standard honest-validator voting flow, degrading throughput/finalization latency of the entire ballot machine (inbound, outbound, chain-meta, TSS, and fund-migration voting all share this same `ActiveBallotIDs` set and `CreateBallot` code path). In the worst case this can push block execution time up significantly and delay finalization of legitimate crosschain observations, which is the functional equivalent of the Y2K "rolloverQueue never gets processed" DoS applied to Push Chain's ballot finalization path.

### Likelihood Explanation
The attacker requires only a deployed, minimally-funded UEA and the ability to submit `MsgExecutePayload` — both are unprivileged, default-path actions with no admin/validator/TSS compromise needed. The only "friction" is the same kind noted by `3xHarry` in the original Y2K discussion: whatever minimal cost exists to generate a batch of distinct trigger events (EVM gas per payload call), which is dramatically cheaper here than in Y2K since it doesn't require real fund transfers, only self-contained EVM calls whose outbound event needs no minimum bridged value.

### Recommendation
- Decouple ballot expiry sweeping from the hot `CreateBallot` path. Do not perform a full `ActiveBallotIDs` walk synchronously inside every new-ballot creation.
- Move expiry processing to a bounded, amortized mechanism (e.g., a `BeginBlocker`/`EndBlocker` that processes at most K expired ballots per block, or an expiry-indexed structure keyed by block height so only ballots actually due for expiry are touched, avoiding a full-collection scan).
- Consider capping the number of concurrently active ballots per observation type, or requiring a minimum bonded/economic cost for cross-chain observation events that lazily create ballots (an analog of Y2K's recommended per-epoch queue mapping / minimum stake to limit spam).

### Proof of Concept
1. Attacker deploys a UEA and funds it with a small amount of native gas.
2. Attacker repeatedly calls `MsgExecutePayload` (gasless, any signer) with payloads whose EVM execution emits a `UniversalTxOutbound` event from the `UniversalGatewayPC` each time, each with unique tx hash/log index → unique `outbound_id` (see `BuildOutboundsFromReceipt`/`attachOutboundsToUtx`).
3. Each new outbound is written to `PendingOutbounds` with no minimum-value gate.
4. As honest UVs vote via `MsgVoteOutbound`, each distinct outbound produces a new ballot ID that has never existed before, triggering `GetOrCreateBallot` → `CreateBallot` → `ExpireBallotsBeforeHeight`, which iterates the entire `ActiveBallotIDs` set and reads every corresponding `Ballots` entry.
5. Repeating steps 2–4 N times grows `ActiveBallotIDs` to size N; the N-th ballot creation costs O(N) keeper work, and the cumulative cost across the campaign is O(N²), imposed on every validator executing these (fee-exempt) vote transactions, while the attacker's own cost stays flat (bounded by cheap EVM gas from their own UEA).

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

**File:** x/uvalidator/keeper/voting.go (L120-146)
```go
func (k Keeper) VoteOnBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	voter string,
	voteResult types.VoteResult,
	voters []string,
	votesNeeded int64,
	expiryAfterBlocks int64,
) (
	ballot types.Ballot,
	isFinalized bool,
	isNew bool,
	err error) {

	k.Logger().Debug("vote on ballot",
		"ballot_id", id,
		"ballot_type", ballotType.String(),
		"voter", voter,
		"vote_result", voteResult.String(),
		"votes_needed", votesNeeded,
	)

	ballot, isNew, err = k.GetOrCreateBallot(ctx, id, ballotType, voters, votesNeeded, expiryAfterBlocks)
	if err != nil {
		return ballot, false, false, errors.Wrap(err, "Error while voting on the ballot")
	}
```

**File:** app/txpolicy/gasless.go (L12-26)
```go
// IsGaslessTx checks if a transaction contains only allowed gasless message types
// Returns true if all messages in the transaction are in the allowed gasless message types
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

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```
