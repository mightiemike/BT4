### Title
Attacker-chosen `revertRecipient` on withdrawal can force `AbortOutbound`, permanently freezing bridged funds with no on-chain recovery - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
The `UniversalGatewayPC` withdraw event lets the initiating user freely choose `revertRecipient` (decoded into `OutboundTx.RevertInstructions.FundRecipient`), which is later used as the re-mint destination if the outbound to the external chain fails. If that address causes `CallPRC20Deposit` to fail (e.g. an address the PRC20 token type refuses to mint to, or an address whose acceptance the token gates), `handleFailedOutbound` calls `AbortOutbound`, permanently marking the outbound `ABORTED` with no automated retry or recovery path — mirroring the C-01 "irreversible exodus mode" pattern of an attacker steering the protocol into a state that requires manual/admin intervention and forever loses the automatic user-refund guarantee.

### Finding Description
`DecodeUniversalTxOutboundFromLog` decodes the `UniversalTxOutbound` event emitted from the user's own Push Chain transaction, taking `revertRecipient` directly from attacker-supplied call data [1](#0-0) . `BuildOutboundsFromReceipt` copies this into `OutboundTx.RevertInstructions.FundRecipient` verbatim, with no validation beyond ABI decoding [2](#0-1) .

When Universal Validators later (honestly) observe that the outbound failed on the destination chain, `handleFailedOutbound` re-mints the bridged PRC20 to `RevertInstructions.FundRecipient` (falling back to `outbound.Sender` only if empty) via `CallPRC20Deposit` [3](#0-2) . If that EVM call fails, the code path is:

```go
if err != nil {
    pcTx.Status = "FAILED"
    ...
    return k.AbortOutbound(ctx, utxId, outbound,
        fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
}
``` [4](#0-3) 

`AbortOutbound` sets `Status_ABORTED` and explicitly documents that "automatic processing has failed and manual intervention is needed" [5](#0-4) . `OutboundTx.ValidateBasic` treats `Status_ABORTED` as a terminal state set only internally, with no validated transition back out of it [6](#0-5) . There is no message or keeper function in the reviewed code that reverses an `ABORTED` outbound or retries the mint — the funds (already burned/withdrawn from the PRC20 supply as part of the original outbound) are stuck, exactly analogous to the C-01 report's "no way to set `isInExodusMode` back to false" complaint, except scoped per-outbound rather than protocol-wide.

An unprivileged, ordinary user submitting a withdrawal (via the standard `executeUniversalTx`/gateway withdraw path) fully controls `revertRecipient`. If they pick an address that is guaranteed to make `depositPRC20Token` revert (for example: the PRC20 token contract's own address, a contract that reverts unconditionally on receiving a call, or — if the underlying PRC20 has any recipient allow/deny-list semantics, akin to the blacklist scenario cited in the original report — a disallowed address), and separately arrange for (or simply wait for) the destination-chain leg of the withdrawal to fail (this is the honest, expected UV-observed outcome for a withdrawal to an unreachable/reverting/blacklisted destination address, which itself can be attacker-chosen), the re-mint step deterministically fails and `AbortOutbound` fires.

### Impact Explanation
This is a reachable, attacker-triggered path to permanently freeze protocol/user funds tied to a specific `UniversalTx`/outbound with no automated recovery, matching the "Out of scope" boundary only if existing guards actually prevent it — here they do not: `RevertInstructions.FundRecipient` is unvalidated attacker input, and `AbortOutbound` is a dead-end state. While the blast radius is scoped to the specific outbound(s) an attacker chooses to sabotage (not a global "exodus mode" halting all deposits/withdrawals protocol-wide as in the original report), it still represents unauthorized, attacker-forced permanent freezing of bridged value with no on-chain remedy, satisfying the "permanent freezing... of user or protocol-controlled funds" allowed-impact category.

### Likelihood Explanation
High for a motivated attacker: submitting a withdrawal with a chosen `revertRecipient` requires no privilege and no validator collusion — only a self-controlled contract/address on the destination side (to make the outbound itself fail, an honestly-observed condition) plus a deliberately "poisoned" `revertRecipient` value. The absence of any validation on `FundRecipient` at decode time or at re-mint time makes this straightforward to trigger deterministically.

### Recommendation
- Validate `RevertInstructions.FundRecipient` at decode/ingestion time (e.g. reject the zero address, the PRC20 contract's own address, and any address flagged as non-EOA/reverting where feasible) before it is persisted onto `OutboundTx`.
- Add a recovery path for `Status_ABORTED` outbounds: either an admin-gated retry-with-different-recipient message (similar in spirit to `MsgRevertStuckInbound`), or fall back automatically to a safe, protocol-controlled escrow account (mapping `utxId => amount` a user can later claim) instead of leaving funds unrecoverable.
- Consider decoupling "the outbound failed on destination chain" from "which address receives the Push-Chain-side refund," e.g. always defaulting the re-mint recipient to the verified original `outbound.Sender` and treating `FundRecipient` only as an optional, validated override.

### Proof of Concept
1. Attacker calls the Push Chain UEA/CEA `executeUniversalTx` (or otherwise triggers `UniversalGatewayPC`'s withdraw path) with `revertRecipient` set to an address chosen to make `depositPRC20Token(prc20, amount, to)` revert (e.g. the PRC20 contract's own address, or another contract engineered to revert unconditionally on any call from the module account).
2. Attacker sets the destination recipient/target such that the outbound is guaranteed to fail on the external chain (attacker controls the destination address/contract).
3. Universal Validators honestly observe the outbound failure and submit `MsgVoteOutbound` with `success=false`; quorum is reached.
4. `FinalizeOutbound` → `handleFailedOutbound` attempts `CallPRC20Deposit(prc20, revertRecipient, amount)`, which reverts because of the crafted `revertRecipient`.
5. `AbortOutbound` is invoked, setting `OutboundTx.OutboundStatus = Status_ABORTED` with `AbortReason` populated. No further automated processing occurs, and the bridged funds tied to this UTX are permanently unrecoverable through any reviewed on-chain path [4](#0-3) .

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L60-92)
```go
		{Type: uint256Type}, // protocolFee
		{Type: addressType}, // revertRecipient
		{Type: uint8Type},   // txType
		{Type: uint256Type}, // gasPrice
	}

	values, err := arguments.Unpack(log.Data)
	if err != nil {
		return nil, fmt.Errorf("failed to unpack UniversalTxOutbound: %w", err)
	}

	if len(values) != 11 {
		return nil, fmt.Errorf("unexpected number of unpacked values: %d", len(values))
	}

	i := 0
	event.ChainId = values[i].(string)
	i++
	event.Target = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.Amount = values[i].(*big.Int)
	i++
	event.GasToken = values[i].(common.Address).Hex()
	i++
	event.GasFee = values[i].(*big.Int)
	i++
	event.GasLimit = values[i].(*big.Int)
	i++
	event.Payload = "0x" + hex.EncodeToString(values[i].([]byte))
	i++
	event.ProtocolFee = values[i].(*big.Int)
	i++
	event.RevertRecipient = values[i].(common.Address).Hex()
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

**File:** x/uexecutor/keeper/outbound.go (L45-69)
```go
// AbortOutbound marks an outbound as ABORTED with a reason.
// This signals that automatic processing has failed and manual intervention is needed.
func (k Keeper) AbortOutbound(ctx context.Context, utxId string, outbound types.OutboundTx, reason string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	outbound.OutboundStatus = types.Status_ABORTED
	outbound.AbortReason = reason

	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Defensively remove from pending index (may already be removed by caller)
	_ = k.PendingOutbounds.Remove(ctx, outbound.Id)

	// Emit event for monitoring/alerting
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"outbound_aborted",
		sdk.NewAttribute("utx_id", utxId),
		sdk.NewAttribute("outbound_id", outbound.Id),
		sdk.NewAttribute("abort_reason", reason),
	))

	return nil
}
```

**File:** x/uexecutor/keeper/outbound.go (L107-119)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/types/outbound_tx.go (L150-151)
```go
	case Status_ABORTED:
		// Set internally by AbortOutbound — no external validation needed.
```
