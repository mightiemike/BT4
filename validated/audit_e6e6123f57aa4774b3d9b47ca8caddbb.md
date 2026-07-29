## Title
`handleFailedOutbound` omits `TxType_GAS` from the fund-revert re-mint set, permanently burning user gas top-up funds on outbound failure - ([File: x/uexecutor/keeper/outbound.go])

### Summary
This is the closest structural analog to the Geodefi `_realizeProcessedEther`/`processValidators` bug: value that has already been irreversibly consumed on Push Chain (PRC20 burned to fund a destination-chain payout) is not guaranteed to be restored when the corresponding "fulfillment" (the outbound broadcast on the destination chain) fails. `TxType.OutboundTx.OutboundStatus` transitions to `REVERTED` after a failed `MsgVoteOutbound`, but the code path that re-mints the burned PRC20 to make the user whole (`handleFailedOutbound`) is gated to a hard-coded subset of `TxType`s.

### Finding Description
`handleFailedOutbound` re-mints bridged tokens **only** for `TxType_FUNDS`, `TxType_GAS_AND_PAYLOAD`, and `TxType_FUNDS_AND_PAYLOAD`: [1](#0-0) 

`TxType_GAS` — documented as "Refund of unused gas back to a source chain" — is conspicuously absent from this list: [2](#0-1) 

When `MsgVoteOutbound` reaches quorum with `observedTx.Success == false` for a `TxType_GAS` outbound, `FinalizeOutbound` routes to `handleFailedOutbound`: [3](#0-2) 

Because `outbound.TxType == types.TxType_GAS` fails the `if` check at the top of `handleFailedOutbound`, the re-mint (`CallPRC20Deposit`) is skipped entirely — execution falls straight through to marking `outbound.OutboundStatus = types.Status_REVERTED` and only the unused-gas-fee refund path (`applyGasRefund`, which refunds a *separate* excess-gas-fee delta, not the bridged principal) runs: [4](#0-3) 

The PRC20/native value that funded the `TxType_GAS` outbound was already burned/deducted at outbound-creation time on Push Chain (mirroring the Geodefi pattern of burning `gETH` in `_realizeProcessedEther` before the corresponding withdrawal is guaranteed to complete). If the destination-chain broadcast subsequently fails (an unprivileged, ordinary outcome reachable via normal relaying/broadcast conditions and voted on honestly by UVs), the outbound is marked `REVERTED` with no code path to restore the burned value to the user — this matches the "funds get stuck / no burn accompanied by a corresponding unwind" bug class from the external report exactly, just with mint/burn roles reversed (burn happens up-front on outbound creation, and the missing counter-action is the re-mint on failure).

### Impact Explanation
This falls squarely within the "Push Chain Allowed Impact Gate": it is a permanent, unrecoverable loss of user/protocol-controlled funds (the value backing a `GAS` outbound) reachable through the honest-validator outbound-voting flow, with no privileged actor involved — only an ordinary destination-chain broadcast failure (gas price spike, nonce collision, relayer transient failure, chain congestion) is required. The `ABORTED` manual-intervention branch only triggers if the re-mint call *itself* errors; for `TxType_GAS` the re-mint is never attempted, so there's no `ABORTED` escape hatch either — the loss is silent and finalized as `REVERTED`.

### Likelihood Explanation
No attacker action is required at all; this is a state-machine gap that fires on any ordinary `GAS` outbound whose destination-chain leg fails and is honestly observed and voted `success=false` by universal validators — a realistic, non-adversarial occurrence in production cross-chain relaying. It requires no privileged or malicious behavior, satisfying the unprivileged-trigger requirement.

### Recommendation
Include `types.TxType_GAS` in the re-mint condition in `handleFailedOutbound` (i.e., treat it the same as `TxType_FUNDS`/`TxType_GAS_AND_PAYLOAD`/`TxType_FUNDS_AND_PAYLOAD`), re-minting the PRC20/native gas-top-up amount back to the sender/`FundRecipient` when a `GAS` outbound fails, consistent with the other value-bearing tx types.

### Proof of Concept
1. A user submits an inbound of `TxType_GAS` on a source chain; Push Chain mints PC to the recipient as a gas top-up (per README table).
2. Later, the corresponding value is burned/deducted to construct a `TxType_GAS` `OutboundTx` (refund of unused gas back to the source chain), created via the normal outbound-creation flow and added to `PendingOutbounds`.
3. Universal Validators broadcast the outbound on the destination chain; the broadcast fails for a mundane reason (e.g., relayer gas spike, RPC timeout, nonce issue).
4. UVs honestly vote `MsgVoteOutbound` with `ObservedTx.Success = false` and reach quorum (see `msg_vote_outbound.go` and `VoteOnOutboundBallot` in `voting.go`).
5. `FinalizeOutbound` → `handleFailedOutbound` runs; since `outbound.TxType == types.TxType_GAS`, the `if` block covering re-mint (lines 104–147 in `outbound.go`) is skipped.
6. `outbound.OutboundStatus` is set to `types.Status_REVERTED` and `UpdateOutbound` persists the record — no PRC20/native re-mint occurred, and the burned value is permanently unrecoverable; only `applyGasRefund`'s unrelated excess-gas-fee delta (if any) is processed. [5](#0-4)

### Citations

**File:** x/uexecutor/keeper/outbound.go (L71-97)
```go
func (k Keeper) FinalizeOutbound(ctx context.Context, utxId string, outbound types.OutboundTx) error {
	// If not observed yet, do nothing
	if outbound.OutboundStatus != types.Status_OBSERVED {
		return nil
	}

	obs := outbound.ObservedTx
	if obs == nil {
		return nil
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Info("finalizing outbound",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"success", obs.Success,
		"dest_chain", outbound.DestinationChain,
		"tx_type", outbound.TxType.String(),
	)

	if !obs.Success {
		return k.handleFailedOutbound(sdkCtx, utxId, outbound, obs)
	}

	return k.handleSuccessfulOutbound(sdkCtx, utxId, outbound, obs)
}
```

**File:** x/uexecutor/keeper/outbound.go (L99-161)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}

	outbound.OutboundStatus = types.Status_REVERTED
	k.Logger().Info("outbound reverted",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)

	// Refund excess gas regardless of tx type — gas was consumed on the external
	// chain whether the execution succeeded or failed.
	k.applyGasRefund(ctx, &outbound, obs)

	return k.UpdateOutbound(ctx, utxId, outbound)
}
```

**File:** x/uexecutor/README.md (L128-136)
```markdown
| `TxType` | Inbound semantics | Outbound semantics |
|---|---|---|
| `GAS` | User pre-paid gas on the source chain. Mints PC to the recipient as a gas top-up. | Refund of unused gas back to a source chain. |
| `GAS_AND_PAYLOAD` | Gas top-up + executes a payload through the recipient's UEA in the same Push Chain tx. | Same combo on the destination side. |
| `FUNDS` | Pure synthetic transfer — mints PRC20 representation of an external token. | Pure transfer of a PRC20 back out of Push Chain. |
| `FUNDS_AND_PAYLOAD` | Mints funds + runs a payload (e.g. deposit + DEX swap atomically). | Funds delivery with a destination-side call. |
| `PAYLOAD` | Pure payload execution, no value movement. | Pure call on the destination chain. |
| `INBOUND_REVERT` | Reverts a previously-executed inbound (returns funds to the source-chain sender). | — |
| `RESCUE_FUNDS` | Admin-driven rescue path for stuck funds. | Outbound that delivers the rescue. |
```
