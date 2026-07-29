## Finding Confirmed

The comment in `universalClient/chains/svm/tx_builder.go` explicitly confirms the mechanism:

> `GetGasFeeUsed` returns "0" for SVM. SVM gas accounting is handled via vault gasFee reimbursement — the actual gas cost is the base fee + PDA rent paid by the relayer, which is reimbursed from the gasFee baked into the signed message. [1](#0-0) 

### Title
Double-payment of destination-chain gas fee for SVM outbounds — `applyGasRefund` refunds the full `gas_fee` to the depositor even though the relayer already collected it on-chain via the vault reimbursement instruction - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
For Solana-destination outbounds, the relayer's gas cost is reimbursed *on the destination chain itself*, as part of the same signed instruction that executes the withdrawal/revert (the `gasFee` parameter baked into `buildWithdrawAndExecuteData`/`buildRevertData`, see `universalClient/chains/svm/tx_builder.go`). Because that reimbursement is already fully settled on Solana, the client's `GetGasFeeUsed` for SVM is hardcoded to always return `"0"`: [1](#0-0) 

That `"0"` becomes `OutboundObservation.GasFeeUsed` in the honest UV's `MsgVoteOutbound`. `x/uexecutor` keeper then computes the "unused" gas as `gasFee - gasFeeUsed = gasFee - 0 = gasFee` (the entire budgeted relayer fee) and mints/refunds that full amount back to `outbound.Sender` (or `RevertInstructions.FundRecipient`) via `applyGasRefund`: [2](#0-1) [3](#0-2) 

This is called for both successful and failed SVM outbounds: [4](#0-3) 

### Finding Description
The root cause is a mismatch in the accounting model, matching the seed bug class ("execution fee refund routed incorrectly, resulting in over-payment / loss to protocol-controlled funds"): the `gasFee` budget is deducted from the bridged amount once, at outbound creation (`event.GasFee` from `UniversalTxOutboundEvent`, decoded in `create_outbound.go`), and is intended to cover the relayer's real cost: [5](#0-4) 

For EVM destination chains, the accounting is consistent: `GetGasFeeUsed` fetches the real receipt cost, so `gasFee - gasFeeUsed` correctly represents genuine leftover budget. [6](#0-5) 

For SVM, however, the actual reimbursement already happened atomically inside the destination-chain transaction (the relayer's on-chain program pulls `gasFee` out of the vault/bridged funds directly, per the `buildRevertData`/`buildWithdrawAndExecuteData` gas_fee field). Because `GetGasFeeUsed` unconditionally reports `"0"` instead of the true value, the Push Chain-side keeper treats the entire `gasFee` as unspent and mints/refunds it a second time to the depositor via `CallUniversalCoreRefundUnusedGas`. This is not a case of an attacker forging state — it is an unprivileged, honest-validator-reachable path (any user who triggers an outbound to Solana) that causes systematic double-counting: the relayer is paid on Solana, and the same amount is separately minted back to the user on Push Chain, corrupting refund/gas-fee accounting and creating unbacked/duplicated value.

### Impact Explanation
Every honestly-processed SVM outbound (revert or withdraw-and-execute, whether the outbound itself succeeds or fails) causes Push Chain to mint an extra `gasFee` amount of PRC20/PC-native tokens to the depositor/fund_recipient that was never actually leftover — it was already consumed by the vault's own relayer reimbursement. This is a protocol-level over-mint (systemic, not attacker-crafted), falling under "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" and "unauthorized mint ... of user or protocol-controlled funds" in the allowed-impact gate. Given SVM outbounds are a standard destination path (not a privileged or edge scenario), this is a continuously reachable, systemic leak of protocol funds.

### Likelihood Explanation
High — this triggers on every successful SVM outbound where `outbound.GasFee` is non-empty and non-zero, which is the default configuration for Solana-bound bridging (`GetOutboundTxGasAndFees` always fetches a non-zero gas fee unless the token config yields zero). No malicious actor is required; honest UVs voting the honest observation (`GasFeeUsed = "0"`) is sufficient to trigger the double-payment on every occurrence.

### Recommendation
For SVM destination chains, either:
1. Have `applyGasRefund` skip the refund entirely when the outbound's destination chain namespace is SVM (since reimbursement already happened on-chain), or
2. Change `GetGasFeeUsed` for SVM to report the true reimbursed amount (equal to `gasFee`, i.e., what was actually paid to the relayer) so that `gasFee - gasFeeUsed` correctly evaluates to `0`, rather than hardcoding `"0"` (which inverts the meaning of the field for this chain family).

Either fix must ensure the "gas_fee_used" semantics are consistent across all destination-chain families so that `applyGasRefund`'s subtraction logic represents genuinely unspent budget, not "we don't track this so assume zero was used."

### Proof of Concept
1. A user submits a Push Chain payload that creates a Solana-destination `OutboundTx` with `GasFee = "111"` (fetched via `GetOutboundTxGasAndFees`) and `GasToken` set to the relayer-fee PRC20.
2. UVs sign and broadcast the outbound using `buildWithdrawAndExecuteData`/`buildRevertData`, which encodes `gasFee=111` into the Solana instruction; the Solana program pays the relayer 111 units directly from vault funds as part of this same transaction.
3. UVs call `GetGasFeeUsed` on the SVM tx builder, which unconditionally returns `"0"` regardless of the real reimbursement: [1](#0-0) 
4. UVs submit `MsgVoteOutbound` with `GasFeeUsed = "0"`. Once threshold is met, `handleSuccessfulOutbound`/`handleFailedOutbound` calls `applyGasRefund`, computing `refundAmount = 111 - 0 = 111` and minting/refunding the full 111 back to the depositor: [7](#0-6) 
5. Net effect: 111 units paid to the relayer on Solana + 111 units minted back to the depositor on Push Chain = 222 units disbursed for a single 111-unit gas budget — a systemic double payment reproducible on every honestly-processed SVM outbound.

### Citations

**File:** universalClient/chains/svm/tx_builder.go (L461-466)
```go
// GetGasFeeUsed returns "0" for SVM. SVM gas accounting is handled via vault
// gasFee reimbursement — the actual gas cost is the base fee + PDA rent paid
// by the relayer, which is reimbursed from the gasFee baked into the signed message.
func (tb *TxBuilder) GetGasFeeUsed(ctx context.Context, txHash string) (string, error) {
	return "0", nil
}
```

**File:** x/uexecutor/keeper/outbound.go (L149-171)
```go
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

// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
```

**File:** x/uexecutor/keeper/outbound.go (L178-206)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** x/uexecutor/keeper/outbound.go (L245-256)
```go
	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
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

**File:** universalClient/chains/evm/tx_builder.go (L452-475)
```go
// GetGasFeeUsed returns the gas fee used by a transaction on the EVM chain.
// Fetches the receipt for gasUsed and the transaction for gasPrice, then returns
// gasUsed * gasPrice as a decimal string. Returns "0" if not found.
func (tb *TxBuilder) GetGasFeeUsed(ctx context.Context, txHash string) (string, error) {
	hash := ethcommon.HexToHash(txHash)
	receipt, err := tb.rpcClient.GetTransactionReceipt(ctx, hash)
	if err != nil {
		return "0", nil
	}

	tx, _, err := tb.rpcClient.GetTransactionByHash(ctx, hash)
	if err != nil {
		return "0", nil
	}

	gasUsed := new(big.Int).SetUint64(receipt.GasUsed)
	gasPrice := tx.GasPrice()
	if gasPrice == nil || gasPrice.Sign() == 0 {
		return "0", nil
	}

	gasFeeUsed := new(big.Int).Mul(gasUsed, gasPrice)
	return gasFeeUsed.String(), nil
}
```
