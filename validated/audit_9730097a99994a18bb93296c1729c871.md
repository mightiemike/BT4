## Finding: Zero destination-chain gas price permanently freezes an outbound (no liquidation/refund path) — analogous to GMX M-20

### Title
Outbounds with a zero `GasPrice` can never be TSS-signed or broadcast, permanently freezing funds with no escape hatch - (File: `universalClient/chains/evm/tx_builder.go`)

### Summary
Exactly like GMX's oracle rejecting a price of zero and thereby making positions unliquidatable, Push Chain's EVM `TxBuilder.GetOutboundSigningRequest` treats `gasPrice == 0` as a hard, unconditional error. Since this function is the single choke point both the TSS coordinator and the co-signers use to build/verify every outbound signing request, an outbound whose `GasPrice` field is `"0"` (or unset) can never be signed, never broadcast, and therefore never reaches the `VoteOutbound` finalization path that would trigger a refund/revert. The entry sits in `PendingOutbounds` forever, and per the module's own documented design, `PendingOutbounds` is **not** removed by ballot expiry and has **no automatic resolution** — only manual/governance intervention.

### Finding Description
`GetOutboundSigningRequest` unconditionally rejects a zero gas price: [1](#0-0) 

This function is the mandatory step for both building the signing payload and verifying it:
- `Coordinator.buildSignTransaction` calls it to construct the DKLS signing setup: [2](#0-1) 
- `SessionManager.verifyOutboundSigningRequest` calls it again on every co-signer to confirm the hash before contributing a signature share: [3](#0-2) 

Both call sites simply propagate the error and abandon that signing attempt; there is no fallback, no minimum-price floor, and no alternate path to produce a signature for that outbound.

On the chain side, `OutboundTx.GasPrice` is populated at outbound-creation time straight from the decoded on-chain event with no non-zero validation: [4](#0-3) 

and the entry is unconditionally written into `PendingOutbounds`: [5](#0-4) 

That `GasPrice` value ultimately derives from `UniversalCore.getOutboundTxGasAndFees`, which is fed by chain-meta gas-price data voted on-chain by UVs — a value that can legitimately be zero (a newly-added chain with no chain-meta vote yet, a chain with a genuinely zero/near-zero base fee, or any transient condition), not necessarily malicious oracle behavior: [6](#0-5) 

Because the module's own documentation explicitly states `PendingOutbounds` has no automatic recovery once created, an outbound stuck at gas-price-zero has no path forward: [7](#0-6) 

The revert/refund logic (`handleFailedOutbound`, `applyGasRefund`) only ever runs after a `VoteOutbound` observation is finalized — but no observation is ever produced because the outbound is never broadcast to begin with: [8](#0-7) 

### Impact Explanation
Any outbound (a `FUNDS` withdrawal, `GAS_AND_PAYLOAD` bridge-out, `INBOUND_REVERT` refund, or `RESCUE_FUNDS` recovery) that is created with `GasPrice == "0"` becomes permanently un-signable and un-broadcastable. The underlying user or protocol funds tied to that `UniversalTx`/`OutboundTx` are frozen indefinitely — no validator vote, admin action, or protocol path exists to move it forward (the only admin recovery function, `RevertStuckInbound`, only operates on stuck **inbound** ballots, not stuck outbounds). This matches the "permanent freezing of user or protocol-controlled funds" and reachable-without-privileged-control impact categories.

### Likelihood Explanation
This does not require a malicious oracle, validator, or relayer — it only requires an ordinary user to trigger an outbound to a destination chain whose chain-meta gas price is (even transiently) zero or not yet populated, which is entirely plausible for newly onboarded chains or chains with subsidized/zero base fees. The guard in `GetOutboundSigningRequest` is precisely the mechanism that turns this ordinary condition into a permanent freeze, mirroring the GMX report's root cause (a validation designed to reject bad values ends up blocking the only recovery mechanism for a critical operation).

### Recommendation
Do not hard-fail outbound signing solely because the recorded gas price is zero. Instead:
- Enforce a non-zero, protocol-defined minimum gas price at outbound-creation time in `x/uexecutor` (reject/clamp before writing to `PendingOutbounds`), and/or
- Add an explicit, chain-driven escape hatch for stuck outbounds (analogous to `RevertStuckInbound`) that lets governance/admin force a revert/refund of `PendingOutbounds` entries that have exceeded their `SigningDeadline` without ever reaching `SIGNED`/`BROADCASTED` state.

### Proof of Concept
1. A destination chain is either newly added or transiently reports (via the chain-meta oracle vote) a gas price of `0`.
2. A user's crosschain withdrawal produces a `UniversalTxOutbound` event with `gasPrice = 0`; `BuildOutboundsFromReceipt`/`attachOutboundsToUtx` create the `OutboundTx` and its `PendingOutbounds` entry with `GasPrice = "0"` — no validation rejects this.
3. The TSS coordinator picks up the pending outbound and calls `buildSignTransaction` → `GetOutboundSigningRequest`, which returns `"gas price is zero or missing in outbound event"` and aborts the signing session.
4. Every subsequent tick retries the same outbound and fails the same way; the outbound never becomes `SIGNED`/`BROADCASTED`, so `VoteOutbound` is never called and no refund/revert is ever executed.
5. The funds represented by that `OutboundTx` remain locked in `PendingOutbounds` permanently, with no on-chain path to recovery.

### Citations

**File:** universalClient/chains/evm/tx_builder.go (L98-106)
```go
	gasPrice := new(big.Int)
	if data.GasPrice != "" {
		if _, ok := gasPrice.SetString(data.GasPrice, 10); !ok {
			return nil, fmt.Errorf("invalid gas price in event data: %s", data.GasPrice)
		}
	}
	if gasPrice.Sign() == 0 {
		return nil, fmt.Errorf("gas price is zero or missing in outbound event")
	}
```

**File:** universalClient/tss/coordinator/coordinator.go (L775-782)
```go
	// Get the signing request (nonce is required for SIGN)
	if assignedNonce == nil {
		return nil, fmt.Errorf("assigned nonce is required for sign transaction")
	}
	signingReq, err := builder.GetOutboundSigningRequest(ctx, &data, *assignedNonce)
	if err != nil {
		return nil, fmt.Errorf("failed to get outbound signing request: %w", err)
	}
```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L955-959)
```go
	// Use coordinator's nonce so our computed hash matches
	signingReq, err := builder.GetOutboundSigningRequest(ctx, &outboundData, req.Nonce)
	if err != nil {
		return fmt.Errorf("failed to get signing request for verification: %w", err)
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L363-371)
```go
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

**File:** x/uexecutor/keeper/gas_fee.go (L26-64)
```go
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
}
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
