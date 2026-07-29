## Native Analog Found [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Shallow, un-reversible confirmation depth allows a source-chain reorg to mint unbacked PRC20 tokens - (File: universalClient/chains/evm/event_confirmer.go)

### Summary
Push Chain's Universal Validators finalize an inbound deposit purely by comparing `latestBlock - receipt.BlockNumber + 1` against a registry-configured confirmation count, then vote it into consensus with no subsequent reorg check. The `REORGED` status exists in the schema but is never set anywhere in production code, so once an event is marked `CONFIRMED` and 2/3 of validators vote it in, the resulting mint/payload execution is irreversible even if the underlying source-chain block is later orphaned.

### Finding Description
The EVM confirmer computes confirmation depth strictly from block-height arithmetic against the latest known head, re-fetching the receipt each poll but never comparing the block hash the event was first observed in against the current canonical block hash at that height: [1](#0-0) 

Once `confirmations >= requiredConfirmations`, the event is flipped straight to `CONFIRMED` and becomes eligible for `VoteInbound`: [4](#0-3) 

`VoteInbound` executes the mint/payload the moment 2/3+ of Universal Validators agree — it performs no additional finality or depth re-check of its own; it trusts the confirmer's earlier `CONFIRMED` judgement: [5](#0-4) [6](#0-5) 

The required depth itself is registry-configured per chain and, in the shipped testnet chain configs, is set to a single confirmation for the primary deposit method (`sendFunds`, `confirmation_type: 1` → `STANDARD`, `standard_inbound: 1`): [7](#0-6) 

`BlockConfirmation.ValidateBasic()` only enforces `fast_inbound <= standard_inbound`; it places no floor on `standard_inbound`, so a 1-block confirmation window is a valid, currently-deployed configuration: [8](#0-7) 

Crucially, the codebase defines a `REORGED` terminal status intended for exactly this scenario, but it is never assigned by any listener, confirmer, or processor — it only appears in a cleanup deletion query and in tests, confirming there is no live reorg-detection/rollback path: [9](#0-8) [10](#0-9) 

Put together: with only 1 required confirmation, a source-chain reorg of depth 1 (common on EVM chains, especially L2/testnet sequencers) can orphan the deposit transaction *after* the Universal Validators have already observed, confirmed, and voted it in — but Push Chain has no mechanism to detect this and reverse the already-executed mint/payload.

### Impact Explanation
This breaks the fundamental collateralization invariant of the bridge: every PRC20/native representation minted on Push Chain must correspond to funds actually locked on the source chain. A 1-confirmation window combined with the total absence of a reorg-invalidation path means an attacker can obtain PRC20 tokens (or trigger payload execution / downstream outbound funds release) backed by a deposit that is later erased from the canonical source chain — an unauthorized mint of protocol-controlled funds with no compensating burn, matching the "unauthorized mint" and "corruption of PRC20 accounting" impact categories in scope.

### Likelihood Explanation
The trigger requires no privileged role — any unprivileged user can submit an ordinary deposit through the gateway on a chain with a shallow `standard_inbound` setting; single-block reorgs are a known, non-adversarial occurrence on several EVM L2s/testnets, and can also be intentionally engineered by an attacker with modest hashpower/relay influence on some networks. The registry admin sets the confirmation depth, but the vulnerability is exploited entirely through the ordinary, permissionless deposit path — the honest Universal Validators and honest core validator converge on the wrong (reorg-invalidated) observation simply because the code never re-checks canonicity after voting.

### Recommendation
1. Enforce a sane minimum floor for `standard_inbound`/`fast_inbound` in `BlockConfirmation.ValidateBasic()` per chain type (do not allow 0/1-block windows for chains without instant finality).
2. In `EventConfirmer.processPendingEvents`, before promoting to `CONFIRMED`, re-verify that the block hash at the recorded height still matches what was originally observed (or re-fetch the receipt and confirm the transaction is still mined at the same block hash), and periodically re-verify already-`CONFIRMED`/pre-vote events against the canonical chain up to the point of the actual `VoteInbound` submission.
3. Implement the dormant `REORGED` path end-to-end: have the listener/confirmer detect vanished or hash-mismatched blocks and transition affected events to `REORGED`, and wire a compensating action (burn/void) for any `UniversalTx` that was already executed off a since-reorged observation.

### Proof of Concept
1. Registry config for chain X sets `sendFunds` confirmation as `STANDARD` with `standard_inbound: 1` (as shipped for `base_sepolia`, `bsc_testnet`, `eth_sepolia`).
2. Attacker submits a `sendFunds` deposit tx; it lands in block N.
3. Block N+1 is produced; `EventConfirmer` computes `confirmations = 2 >= 1` and marks the event `CONFIRMED` [11](#0-10) .
4. Universal Validators call `VoteInbound`; 2/3 threshold is reached and the core validator mints PRC20 to the attacker's UEA / executes the payload [12](#0-11) .
5. Source chain reorgs at depth 1, orphaning block N (and the deposit tx with it). No code path detects this or reverses the mint — `StatusReorged` is never set [10](#0-9) .
6. Attacker now holds PRC20 tokens unbacked by any real source-chain deposit and can freely move/redeem them via the normal gasless `MsgExecutePayload` path.

Note: I was unable to fully verify how frequently 1-confirmation/0-confirmation values propagate to every mainnet chain config (only testnet-donut configs were indexed), so real-world exploitability depends on the specific `BlockConfirmation` values chosen per chain by the `uregistry` admin at deployment time.

### Citations

**File:** universalClient/chains/evm/event_confirmer.go (L159-165)
```go
		}

		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1

		if confirmations >= requiredConfirmations {
```

**File:** universalClient/chains/evm/event_confirmer.go (L203-206)
```go
				rowsAffected, err = ec.chainStore.UpdateStatusAndEventData(event.EventID, store.StatusPending, store.StatusConfirmed, updatedData)
			} else {
				rowsAffected, err = ec.chainStore.UpdateEventStatus(event.EventID, store.StatusPending, store.StatusConfirmed)
			}
```

**File:** universalClient/store/models.go (L10-20)
```go
// Event status values.
const (
	StatusPending     = "PENDING"     // Observed on external chain, awaiting confirmations
	StatusConfirmed   = "CONFIRMED"   // Confirmed (ready for processing or voting)
	StatusInProgress  = "IN_PROGRESS" // TSS signing in progress
	StatusSigned      = "SIGNED"      // TSS signing done, tx not yet broadcast
	StatusBroadcasted = "BROADCASTED" // Transaction sent to external chain
	StatusCompleted   = "COMPLETED"   // Successfully completed
	StatusReverted    = "REVERTED"    // Failed (expiry, receipt failed, or vote failed)
	StatusReorged     = "REORGED"     // Removed due to chain reorganization
)
```

**File:** universalClient/chains/common/chain_store.go (L175-194)
```go
// DeleteTerminalEvents deletes events in terminal states (COMPLETED, REVERTED, EXPIRED)
// that were updated before the given time
func (cs *ChainStore) DeleteTerminalEvents(updatedBefore any) (int64, error) {
	if cs.database == nil {
		return 0, fmt.Errorf("database is nil")
	}

	// Unscoped() = hard delete (free disk). Without it, GORM does a soft
	// delete (just sets deleted_at), which defeats the cleaner's purpose.
	res := cs.database.Client().Unscoped().
		Where("status IN ? AND updated_at < ?",
			[]string{store.StatusCompleted, store.StatusReorged, store.StatusReverted}, updatedBefore).
		Delete(&store.Event{})

	if res.Error != nil {
		return 0, fmt.Errorf("failed to delete terminal events: %w", res.Error)
	}

	return res.RowsAffected, nil
}
```

**File:** x/uexecutor/keeper/voting.go (L11-61)
```go
func (k Keeper) VoteOnInboundBallot(
	ctx context.Context,
	universalValidator sdk.ValAddress,
	inbound types.Inbound,
) (isFinalized bool,
	isNew bool,
	err error) {
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return false, false, err
	}

	universalValidatorSet, err := k.uvalidatorKeeper.GetEligibleVoters(ctx)
	if err != nil {
		return false, false, err
	}

	// number of validators
	totalValidators := len(universalValidatorSet)

	// votesNeeded = ceil(2/3 * totalValidators)
	// >2/3 quorum similar to tendermint
	votesNeeded := (types.VotesThresholdNumerator*totalValidators)/types.VotesThresholdDenominator + 1

	k.Logger().Debug("voting on inbound ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)

	// Convert []sdk.ValAddress → []string
	universalValidatorSetStrs := make([]string, len(universalValidatorSet))
	for i, v := range universalValidatorSet {
		universalValidatorSetStrs[i] = v.IdentifyInfo.CoreValidatorAddress
	}

	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
	if err != nil {
		return false, false, err
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L86-107)
```go
	// --- Ballot finalized: always create UTX from here on ---
	k.Logger().Info("inbound ballot finalized, creating utx", "utx_key", universalTxKey, "source_chain", inbound.SourceChain)

	// Normalize inbound after finalization: strip irrelevant fields, decode raw_payload.
	// If normalization/decode fails, create UTX with failed PCTx + revert.
	if normalizeErr := inbound.NormalizeForTxType(); normalizeErr != nil {
		k.Logger().Warn("inbound normalization failed after ballot finalization",
			"utx_key", universalTxKey,
			"error", normalizeErr.Error(),
		)
		utx := types.UniversalTx{Id: universalTxKey, InboundTx: &inbound}
		if createErr := k.CreateUniversalTx(ctx, universalTxKey, utx); createErr != nil {
			return createErr
		}
		if removeErr := k.RemovePendingInbound(ctx, inbound); removeErr != nil {
			return removeErr
		}
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, normalizeErr); handleErr != nil {
			return handleErr
		}
		return nil
	}
```

**File:** config/testnet-donut/base_sepolia/chain.json (L6-17)
```json
  "block_confirmation": {
    "fast_inbound": 0,
    "standard_inbound": 1
  },
  "gas_oracle_fetch_interval": "30s",
  "gateway_methods": [
    {
      "name": "sendFunds",
      "identifier": "0x65f4dbe1",
      "event_identifier": "0xd9074957cd6846aa1b09b2e676dac3b9cdeecabd643cabd3d0a0f41e2acd1c50",
      "confirmation_type": 1
    },
```

**File:** x/uregistry/types/block_confirmation.go (L20-27)
```go
// ValidateBasic performs sanity checks on the BlockConfirmation
func (p BlockConfirmation) ValidateBasic() error {
	if p.FastInbound > p.StandardInbound {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "fast_inbound cannot be greater than standard_inbound confirmations")
	}

	return nil
}
```
