## Analysis

The external report's bug class is: **value that flows into a contract as legitimate "excess"/refund accounting gets permanently trapped because the refund path can fail with no retry, no error propagation, and no sweep mechanism.**

Push Chain has a direct structural analog in the outbound gas-refund accounting path in `x/uexecutor`.

### Title
Failed excess-gas refunds are permanently stranded in UniversalCore with no retry or sweep path - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
When an outbound finalizes (success or failure), `applyGasRefund` computes `gasFee - gasFeeUsed` and, if positive, attempts to return the excess bridged gas-token value to the user via `CallUniversalCoreRefundUnusedGas` — first with a swap to native PC, then falling back to a direct PRC20 deposit. If **both** attempts fail, the code only records `PcRefundExecution.Status = "FAILED"` and `RefundSwapError`, and returns normally — the outbound stays finalized (`REVERTED`/`OBSERVED`) with no revert, no queued retry, and no admin/user-callable sweep to recover the stuck excess value. [1](#0-0) 

### Finding Description
`applyGasRefund` is invoked from both `handleFailedOutbound` and `handleSuccessfulOutbound` for every finalized outbound that has a positive `gasFee - gasFeeUsed` delta: [2](#0-1) 

The refund recipient is attacker/user-influenced: it defaults to `outbound.Sender` but is overridden by `outbound.RevertInstructions.FundRecipient` when set — a field populated from user-supplied inbound data. [3](#0-2) 

Both refund attempts route through `CallUniversalCoreRefundUnusedGas`, which calls the `UniversalCore.refundUnusedGas` contract method holding the escrowed gas-token value: [4](#0-3) 

If the swap-path fee-tier/quote lookup fails (unregistered/thin-liquidity gas token) **and** the no-swap fallback deposit also fails (e.g. the refund recipient is a contract that reverts on receiving the PRC20 — fully controllable via `RevertInstructions.FundRecipient`), the function sets `Status = "FAILED"` and simply returns: [5](#0-4) 

No caller inspects `PcRefundExecution.Status` to trigger a retry, revert, or alternate recipient path — this is confirmed by the integration tests, which explicitly assert that a refund failure does not change outbound status and is simply recorded: [6](#0-5) 

A codebase-wide search for any retry, claim, or sweep mechanism referencing `PcRefundExecution`/`RefundSwapError` outside of this file and its tests returns nothing — there is no follow-up flow (no `MsgRetryRefund`, no keeper hook, no periodic sweeper) to reclaim these funds once `refundUnusedGas` fails both ways.

### Impact Explanation
The excess gas-token value that `UniversalCore` holds in escrow for the refund becomes permanently stuck protocol/user-controlled funds with no on-chain path to retrieve it — the same fund-class impact as the referenced LamboVEthRouter finding (irretrievable accumulated value due to missing sweep logic). Because `FundRecipient` is attacker-influenced, an attacker can deliberately force the no-swap fallback to fail (e.g., point `FundRecipient` at a contract designed to revert on PRC20/token receipt) while also causing (or exploiting an existing) swap-path failure for a given gas token, guaranteeing the refund is permanently dropped for their own or others' outbounds, at protocol expense.

### Likelihood Explanation
Triggering requires only unprivileged normal usage: submit any inbound/outbound flow with a `RevertInstructions.FundRecipient` pointing at a contract that reverts on token transfer, combined with a gas token that lacks a configured Uniswap fee tier or sufficient liquidity (a state reachable without any privileged action, since token/fee-tier configuration gaps or thin liquidity are common for newly bridged gas tokens). No malicious validator, TSS, or admin behavior is needed — only the ordinary finalize-outbound path with attacker-chosen recipient/token combination.

### Recommendation
Add an explicit escape-hatch for failed refunds: when both the swap and no-swap `refundUnusedGas` calls fail, either (a) queue the amount into a per-recipient claimable ledger with a `MsgClaimStuckRefund` message unprivileged users can call once the underlying issue (liquidity, recipient contract) is fixed, or (b) fall back to crediting the module-tracked recipient address directly rather than an attacker-chosen contract recipient, and/or (c) add an admin-recoverable sweep analogous to `RevertStuckInbound` for outbound refunds that persistently fail.

### Proof of Concept
1. Attacker submits an inbound/outbound flow where `RevertInstructions.FundRecipient` is set to a contract address they control that unconditionally reverts on receiving the PRC20 gas token (or any ERC20-style transfer).
2. Choose (or wait for) a gas token for which `GetDefaultFeeTierForToken`/`getSwapQuoteForRefund` fails or returns unusable liquidity (see `x/uexecutor/keeper/outbound.go:214-237`).
3. Drive the outbound to finalization with `gasFeeUsed < gasFee` so `applyGasRefund` computes a positive `refundAmount` (`x/uexecutor/keeper/outbound.go:194-198`).
4. Both the swap and no-swap `CallUniversalCoreRefundUnusedGas` calls fail; `PcRefundExecution.Status = "FAILED"` is recorded and the function returns with no further action (`x/uexecutor/keeper/outbound.go:245-256`).
5. Verify no subsequent module logic, message handler, or ABCI hook ever revisits this outbound to retry or reclaim the refund — the `refundAmount` in `UniversalCore` remains permanently unaccounted for and unrecoverable.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L163-196)
```go
// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
}

// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
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
```

**File:** x/uexecutor/keeper/outbound.go (L201-206)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** x/uexecutor/keeper/outbound.go (L239-256)
```go
	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

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

**File:** x/uexecutor/keeper/evm.go (L595-644)
```go
// CallUniversalCoreRefundUnusedGas calls refundUnusedGas on UniversalCore to return excess gas fee
// to the recipient. withSwap=true swaps the gas token back to PC; withSwap=false deposits PRC20 directly.
func (k Keeper) CallUniversalCoreRefundUnusedGas(
	ctx sdk.Context,
	gasToken common.Address,
	amount *big.Int,
	recipient common.Address,
	withSwap bool,
	fee *big.Int,
	minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
	)
}
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L192-197)
```go
		// Refund execution must always be recorded when excess gas exists
		require.NotNil(t, ob.PcRefundExecution)

		// The outbound status stays OBSERVED (refund failure does not revert the outbound)
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)
	})
```
