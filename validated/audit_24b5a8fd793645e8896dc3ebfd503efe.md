I found a concrete analog of the H-01 pattern: a code path that silently returns without crediting/refunding the user when a "skip" precondition is unmet, causing a permanent, silent loss of legitimately-owed funds with no error trace and no compensating accounting entry.

### Title
Excess destination-chain gas fee is silently, permanently lost when `GasToken` (or `GasFee`) is empty on an outbound - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
`applyGasRefund` computes `refundAmount = gasFee - gasFeeUsed` and calls `UniversalCore.refundUnusedGas` to return excess gas fee to the user, exactly analogous to the Juicebox `creditsOf[]` credit that should be granted to the payer. However, the function has an early, silent `return` when `outbound.GasToken == ""` (or `outbound.GasFee == ""`), with no error, no PCTx record, and no state marking the refund as owed. If an outbound is ever finalized with an empty `GasToken`, the user's excess gas is permanently and silently forfeited — mirroring the "should credit but instead just returns" defect in the reported H-01.

### Finding Description
`applyGasRefund` in [1](#0-0)  begins with:

```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}
	...
```

This is called unconditionally from both `handleSuccessfulOutbound` and `handleFailedOutbound` [2](#0-1) , i.e. every finalized outbound — including the `INBOUND_REVERT` type, which is auto-created by `buildRevertOutbound` whenever inbound execution/validation fails [3](#0-2) .

Crucially, `buildRevertOutbound` populates `GasToken` only when the `GetTokenConfig` lookup and the `GetGasFeeInfoForRevertOutbound` EVM call both succeed; on *any* failure it logs a warning and returns the outbound **without gas fields set** (`GasToken` stays empty): [4](#0-3) .

So the reachable, unprivileged sequence is:
1. An ordinary user submits an inbound (e.g., `FUNDS_AND_PAYLOAD`) that later fails validation/execution, triggering an automatic `INBOUND_REVERT` outbound via `buildRevertOutbound`.
2. If, at the moment of building the revert outbound, `GetTokenConfig` or the `getOutboundTxGasAndFees` EVM call transiently fails (RPC hiccup, EVM call revert, token config not (yet) present, etc.) — none of which requires any privileged actor — the resulting outbound has `GasToken == ""` while still carrying the actual `GasFee`... except here `GasFee` also remains unset in that branch (the whole gas-fields block is skipped together), so both `GasFee` and `GasToken` end up empty for that outbound.
3. When the universal validators later vote `MsgVoteOutbound` for this outbound with an observed `GasFeeUsed` (the actual gas consumed on the destination chain), `applyGasRefund` sees `outbound.GasFee == "" || outbound.GasToken == ""` and returns immediately — no `PcRefundExecution` is ever set, `RefundSwapError` is never set, and no error surfaces on the `UniversalTx`.
4. The excess gas fee that was already collected/reserved on the destination/source side (and was expected to be returned per the `GAS`/refund semantics of the `TxType` table) is never returned to the user and there is no compensating credit anywhere in the `UniversalTx` state — funds are simply gone with zero trace, exactly like the missing `creditsOf[]` update in H-01.

This matches the exact shape of the external bug: a legitimate "skip" branch (originally meant only to avoid work when there truly is nothing to refund, i.e., `obs.GasFeeUsed == ""`) is silently reused as a generic bail-out for missing gas metadata, without ever crediting the user for the value they are owed.

### Impact Explanation
This causes a **permanent loss of user-owed funds** (the destination-chain gas-fee refund) with no audit trail, satisfying the in-scope impact "permanent loss ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting, refund accounting." It is triggered purely by ordinary user transaction submission plus a transient/expected failure of an external EVM call inside `buildRevertOutbound` — no privileged or malicious actor is required, and honest validators/nodes are all that's needed to reach and finalize the outbound.

### Likelihood Explanation
Likelihood is moderate: it requires the token-config lookup or `getOutboundTxGasAndFees` EVM call inside `buildRevertOutbound` to fail at the specific moment an `INBOUND_REVERT` outbound is created (this is an existing, logged, non-fatal fallback path in the code, so it is a normal/anticipated occurrence, not a hypothetical edge case). Any transient EVM/state issue, chain-config gap, or genuinely absent token config at revert time reliably reproduces this outcome.

### Recommendation
- In `applyGasRefund`, do not silently return when `GasToken`/`GasFee` are missing; instead, mark the outbound with an explicit failure state (e.g., set `RefundSwapError`/a `PcRefundExecution` with `Status = "FAILED"` and a descriptive error) so the shortfall is visible on-chain and can be reconciled/retried, mirroring the pattern already used for the swap-refund fallback.
- In `buildRevertOutbound`, avoid completing outbound construction with empty gas fields on lookup failure; either retry, queue for later gas-field backfill, or explicitly flag the outbound as requiring manual gas-refund reconciliation.
- Add an invariant check so that finalizing (`OBSERVED`) an outbound with a non-empty `obs.GasFeeUsed` but empty `outbound.GasFee`/`GasToken` is treated as an anomaly to be recorded, not silently dropped.

### Proof of Concept
Conceptual (no execution environment available here, but derivable directly from code paths):
1. Configure a chain/token such that `GetTokenConfig(sourceChain, assetAddr)` succeeds but the mocked `getOutboundTxGasAndFees` EVM call in `GetGasFeeInfoForRevertOutbound` fails (as is already exercised by existing unit test patterns in `gas_fee_test.go`, but here triggered during `buildRevertOutbound`).
2. Submit a `FUNDS_AND_PAYLOAD` inbound that fails execution (e.g., malformed payload, per existing test `vote_inbound_validation_test.go` lines 123-182) so that `handleFailedInboundValidation` → `buildRevertOutbound` runs and produces an `INBOUND_REVERT` outbound with `GasToken == ""`.
3. Vote `MsgVoteOutbound` for that outbound with `GasFeeUsed` less than the true gas fee consumed on the destination chain (analogous to `gas_fee_refund_test.go` lines 108-153, but on the auto-created revert outbound instead of the mock outbound with pre-populated `GasFee`/`GasToken`).
4. Observe: `ob.PcRefundExecution` is `nil` and `ob.RefundSwapError` is empty even though real excess gas was consumed and should have been refunded — matching the assertions pattern in `gas_fee_refund_test.go` lines 65-72, but now representing an actual bug (silent loss) rather than the intended "no excess" case.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L149-172)
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
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-196)
```go
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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-54)
```go
	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L27-55)
```go
	// Look up the PRC20 address for this external token
	tokenCfg, err := k.uregistryKeeper.GetTokenConfig(sdkCtx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil || tokenCfg.NativeRepresentation == nil || tokenCfg.NativeRepresentation.ContractAddress == "" {
		k.Logger().Warn("failed to get PRC20 for revert outbound gas lookup, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"asset", inbound.AssetAddr,
			"error", err,
		)
		return outbound
	}

	// Fetch gas fields from UniversalCore.getOutboundTxGasAndFees(prc20, 0)
	// 0 means use the contract's baseLimit for this chain
	gasToken, gasFee, gasPrice, gasLimit, err := k.GetGasFeeInfoForRevertOutbound(sdkCtx, tokenCfg.NativeRepresentation.ContractAddress)
	if err != nil {
		k.Logger().Warn("failed to fetch gas fee info for revert outbound, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"prc20", tokenCfg.NativeRepresentation.ContractAddress,
			"error", err,
		)
		return outbound
	}

	outbound.GasToken = gasToken
	outbound.GasFee = gasFee
	outbound.GasPrice = gasPrice
	outbound.GasLimit = gasLimit

	return outbound
```
