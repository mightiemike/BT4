### Title
Unbounded `ExpireBallotsBeforeHeight` scan on every new ballot creation enables attacker-triggered gas-exhaustion DoS of the inbound/outbound ballot machine - (File: x/uvalidator/keeper/ballot.go)

### Summary
`CreateBallot` unconditionally calls `ExpireBallotsBeforeHeight`, which iterates over the *entire* `ActiveBallotIDs` collection every time any new ballot is created [1](#0-0) . Since `ActiveBallotIDs`/`Ballots` is a single shared collection used by inbound, outbound, chain-meta, TSS, and fund-migration ballots alike [2](#0-1) , and since inbound ballots are created deterministically from source-chain events that an unprivileged external attacker fully controls (any deposit tx on a watched chain), an attacker can cheaply flood a source chain with many distinct small-value inbound events. Honest Universal Validators are then forced to submit `MsgVoteInbound` for each event, and each such vote (via `GetOrCreateBallot` → `CreateBallot`) walks the full `ActiveBallotIDs` set — an O(n) operation per vote that grows linearly with the number of not-yet-expired ballots the attacker has caused to accumulate. This is the same "push pattern" for-loop class described in the report (unbounded iteration triggered on the critical finalize/vote path), just realized in the ballot machine instead of a Solidity contract.

### Finding Description
The generic ballot machine keeps one `ActiveBallotIDs` key-set shared by every ballot type: `INBOUND_TX`, `OUTBOUND_TX`, `CHAIN_META`, `TSS_EVENT`, and `FUND_MIGRATION` [3](#0-2) .

Every time `VoteOnBallot` is invoked for a *new* ballot ID, it calls `GetOrCreateBallot`, which calls `CreateBallot` on a miss [4](#0-3) . `CreateBallot` unconditionally runs `ExpireBallotsBeforeHeight(ctx, blockHeight)` before creating the new ballot: [1](#0-0) 

`ExpireBallotsBeforeHeight` iterates the whole `ActiveBallotIDs` iterator and, for every ID found, performs an additional `Ballots.Get` lookup to check its expiry height: [5](#0-4) 

This is a classic push-pattern for-loop: the cost of creating *any* new ballot scales with the total count of currently-active (not-yet-expired) ballots system-wide, not with anything bounded by the caller.

Inbound ballot IDs are deterministically derived from externally-observed source-chain events (`ballotID = hex(marshal(Inbound))`, keyed off `sha256(source_chain:tx_hash:log_index)`) [6](#0-5) . An attacker fully controls the volume and cadence of these events by simply sending many transactions to the watched gateway contract on an external chain — no special permission, no admin key, and no cooperation from a malicious validator is required; only *honest* Universal Validators observing and voting the events as designed via `MsgVoteInbound` [7](#0-6) .

Because each unique inbound event produces a distinct ballot ID, and ballots only leave `ActiveBallotIDs` when finalized or expired — and expiry itself is driven only by this same O(n) scan being invoked from `CreateBallot` — an attacker who submits new inbound-triggering events faster than validators/blocks can expire the backlog causes `ActiveBallotIDs` to grow without bound. Every subsequent `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta`/TSS/fund-migration vote that needs to create a *new* ballot pays the full O(n) cost, since they all share the same `ActiveBallotIDs` collection.

### Impact Explanation
If the backlog grows large enough that a single `ExpireBallotsBeforeHeight` scan exceeds the block/tx gas budget, no new ballot can ever be created again — which halts the entire universal-execution pipeline: new inbound deposits cannot be tallied (PRC20 mint/UEA payload execution blocked), new outbound TSS-signing/broadcast ballots cannot be created, and chain-meta/TSS/fund-migration voting is likewise blocked, since they all share the same `ActiveBallotIDs` scan. This matches the "denial of service... reachable without privileged control" and "permanent freezing of user or protocol-controlled funds" impact categories: user deposits already in flight, and pending outbound withdrawals, would be stuck indefinitely with no honest-validator-only remedy, mirroring the original report's `finalizeGame()`/`closeGame()` DoS via unbounded loops.

### Likelihood Explanation
The trigger requires no privileged access — only the ability to submit transactions on an external chain the gateway watches, which is available to any unprivileged user (e.g., depositing dust amounts repeatedly). The only limiting factor is the cost of generating a large number of distinct source-chain events (external chain gas fees), and whether ballot expiry windows (`expiryAfterBlocks`, `DefaultExpiryAfterBlocks`) allow the backlog to shrink faster than the attacker can grow it. Since expiry cleanup itself piggybacks on the same vulnerable O(n) scan (only triggered opportunistically as new ballots are created, not proactively pruned in an `EndBlocker`), a sustained flood can keep the active set growing. Exact economic thresholds (external chain gas cost vs. accumulated ballot count needed to exceed block gas limits) were not fully modeled here and would need empirical/gas-benchmarking verification to confirm a hard DoS versus a degraded-performance issue.

### Recommendation
Decouple ballot expiry from the hot "create new ballot" path:
- Run ballot expiration in a dedicated `EndBlocker`/`BeginBlocker` with a bounded per-block work budget (process at most N stale ballots per block) instead of scanning the whole `ActiveBallotIDs` set synchronously inside `CreateBallot`.
- Consider partitioning `ActiveBallotIDs` per ballot type (or per chain) so a flood of inbound ballots cannot starve outbound/TSS/chain-meta ballot creation.
- Add a rate limit / minimum-value threshold on inbound events eligible for ballot creation to raise the attacker's cost of generating spam ballots.
- Alternatively, index active ballots by expiry height (e.g., a sorted/height-keyed collection) so expiry lookups are O(expired count) rather than O(all active ballots).

### Proof of Concept
1. Attacker identifies a Push Chain–watched external chain/gateway with a low `FastInbound`/`StandardInbound` confirmation requirement.
2. Attacker submits a large number (N) of minimal-value deposit transactions to the gateway in quick succession, each producing a distinct `(source_chain, tx_hash, log_index)` triple.
3. Honest Universal Validators observe and submit `MsgVoteInbound` for each of the N events as designed.
4. Each `MsgVoteInbound` that is the first vote for its event calls `GetOrCreateBallot` → `CreateBallot` → `ExpireBallotsBeforeHeight`, which iterates the full `ActiveBallotIDs` set (growing toward N) and does a `Ballots.Get` per entry.
5. As N grows, per-vote gas cost grows linearly; once it approaches the tx/block gas limit, subsequent `MsgVoteInbound`/`MsgVoteOutbound`/other ballot-machine votes fail or get excluded from blocks, stalling inbound execution and outbound TSS signing chain-wide — funds in flight remain stuck with no user-triggerable remedy (per the module's documented "no safe automatic resolution" for pending outbounds) [8](#0-7) .

Note: this is a reasoned architectural analysis based on the retrieved code; I was not able to run a live gas benchmark to confirm the exact backlog size needed to trip the block gas limit, and I did not find an existing rate-limit or minimum-deposit-amount guard in the reviewed registry/gateway configuration that would mitigate this.

### Citations

**File:** x/uvalidator/keeper/ballot.go (L36-39)
```go
	// First, expire any old ballots before this height
	if err := k.ExpireBallotsBeforeHeight(ctx, blockHeight); err != nil {
		return types.Ballot{}, err
	}
```

**File:** x/uvalidator/keeper/ballot.go (L70-89)
```go
// GetOrCreateBallot returns the ballot if it exists, otherwise creates it.
func (k Keeper) GetOrCreateBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	voters []string,
	votesNeeded int64,
	expiryAfterBlocks int64,
) (types.Ballot, bool, error) {

	if ballot, err := k.Ballots.Get(ctx, id); err == nil {
		k.Logger().Debug("ballot found (existing)", "ballot_id", id)
		return ballot, false, nil
	}

	k.Logger().Debug("ballot not found, creating new", "ballot_id", id, "ballot_type", ballotType.String())
	newBallot, err := k.CreateBallot(ctx, id, ballotType, voters, votesNeeded, expiryAfterBlocks)

	return newBallot, true, err
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

**File:** x/uvalidator/README.md (L32-55)
```markdown
### Generic Ballot Machine

Every crosschain observation (in `x/uexecutor` and `x/utss`) is voted through this single mechanism:

```go
ballot, finalized, isNew, err := k.VoteOnBallot(
    ctx,
    ballotId,           // canonical hash of the observation
    ballotType,         // INBOUND | OUTBOUND | CHAIN_META | TSS_EVENT | FUND_MIGRATION
    voter,              // signer's bech32 address
    voteResult,         // SUCCESS | FAILURE
    eligibleVoters,     // snapshot of UVs at ballot creation
    votesNeeded,        // threshold (caller decides 2/3, 100%, simple majority, ...)
    expiryAfterBlocks,  // ballot auto-expires after this many blocks
)
```

A ballot is created lazily on the first vote, indexed in `ActiveBallotIDs`, and finalizes the moment either:
- `yesVotes >= votingThreshold` -> `BALLOT_STATUS_PASSED`
- `eligibleVoters - noVotes < votingThreshold` (the threshold is now mathematically unreachable) -> `BALLOT_STATUS_REJECTED`

On finalization, the ballot is moved from `ActiveBallotIDs` -> `FinalizedBallotIDs`. Expired ballots that never reached threshold are moved to `ExpiredBallotIDs`.

The ballot type is opaque — `x/uvalidator` doesn't care what's being voted on. The ballot ID is a `sha256` of the canonical observation, so two validators voting on the same observation hit the same ballot deterministically.
```

**File:** x/uexecutor/keeper/inbound.go (L20-21)
```go
// utx_key = sha256(source_chain:tx_hash:log_index) — see GetInboundUniversalTxKey.
// ballotID = hex(marshal(Inbound)) — see GetInboundBallotKey.
```

**File:** x/uexecutor/README.md (L151-209)
```markdown

### `Status` — per-outbound status

`OutboundTx.outbound_status` uses a separate, narrower enum:

| `Status` | Meaning |
|---|---|
| `PENDING` | Outbound created on Push Chain, waiting for UVs to broadcast and vote |
| `OBSERVED` | UVs voted the outbound was successfully broadcast on the destination chain |
| `REVERTED` | UVs voted the outbound permanently failed; revert path triggered |
| `ABORTED` | Finalization or revert attachment failed and requires manual intervention |

### Lifecycle Walkthrough

A typical `FUNDS_AND_PAYLOAD` inbound, end to end:

```
1. UV observes a source-chain gateway event.
2. UV submits MsgVoteInbound. The UTX is created the moment the first vote
   arrives, with id = sha256(sourceChain:txHash:logIndex). Only the
   InboundTx field is populated; PcTx and OutboundTx are empty.
   (UTX id is also added to PendingInbounds.)

3. Threshold of UV votes reached. The keeper executes the inbound:
   a. Mints the PRC20 to the recipient's UEA address.
      A new PCTx (deposit) is appended to UTX.PcTx.
   b. Runs the universal payload through the UEA.
      A second PCTx (executeUniversalTx) is appended.
   (UTX id removed from PendingInbounds.)

4. The payload triggered a destination-chain call (e.g. release funds on
   another chain). An OutboundTx is created with Status_PENDING and
   appended to UTX.OutboundTx. It is also indexed in PendingOutbounds.

5. UVs sign the outbound via TSS, broadcast it, and vote the result back
   via MsgVoteOutbound. The OutboundTx.observed_tx is filled in and
   outbound_status flips to OBSERVED. The PendingOutbounds entry is
   removed.

6. If the destination chain refunds excess gas, a refund PCTx runs on
   Push Chain. PCTx.pc_refund_execution is set on the OutboundTx. The
   refund is just additional evidence attached to the existing OutboundTx.
```

At every step the UTX is mutated **append-only**: new entries are added to `pc_tx` and `outbound_tx`, existing entries are updated in place, and the live state of those slices is the only source of truth for "what's happening" with this UTX.

## Messages (`MsgServer`)

| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.

Vote messages check `IsBondedUniversalValidator` and `IsTombstonedUniversalValidator` on `x/uvalidator` before accepting the vote. Tombstoned validators are silently rejected.
```

**File:** x/uexecutor/README.md (L273-282)
```markdown
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```
