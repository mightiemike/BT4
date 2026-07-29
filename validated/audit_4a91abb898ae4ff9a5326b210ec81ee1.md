### Title
Truncating base-fee unwrap in `CalculateGasCost` silently under-collects gas fees from UEA-routed universal payloads - (File: x/uexecutor/keeper/fees.go)

### Summary
The external report flags Solidity math libraries that omit `unchecked`, causing them to revert on values that should legally overflow/wrap during "phantom" intermediate arithmetic. The Go analog in Push Chain's scope is the mirror-image defect: `CalculateGasCost` in `x/uexecutor/keeper/fees.go` performs a lossy integer unwrap of the `LegacyDec` base fee that silently truncates any fractional-`upc` component instead of preserving it, corrupting the gas-fee accounting invariant that is supposed to make gas billing exact.

### Finding Description
`CalculateGasCost` computes the fee billed to a user's UEA account for a `DerivedEVMCall`: [1](#0-0) 

The base fee arrives as an `sdkmath.LegacyDec` (18-decimal fixed point) from `k.feemarketKeeper.GetBaseFee(sdkCtx)`. The code assumes, per its own comment, that "base fee is always a whole number of upc — no fractional upc exists," and unwraps it with plain integer division:

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

This assumption does not hold for a standard EIP-1559-style fee market: `x/feemarket`'s base fee is adjusted every block by a multiplicative ratio (e.g. `baseFee * (1 ± gasUsed/gasTarget * 1/denominator)`), which routinely produces `LegacyDec` values that are **not** exact multiples of `1e18`. `big.Int.Div` truncates toward zero, so any fractional-`upc` remainder in the true base fee is silently discarded before `effectiveGasPrice` is computed: [2](#0-1) 

`gasCost = effectiveGasPrice * gasUsed` is then deducted from the recipient UEA via `DeductGasFeesFromReceipt` → `DeductAndBurnFees`: [3](#0-2) 

Because `effectiveGasPrice` is always rounded **down** to the nearest whole `upc`, every `DerivedEVMCall` billed through `ExecutePayloadV2` / `ExecutePayload` (`x/uexecutor/keeper/execute_payload.go`, `x/uexecutor/keeper/msg_execute_payload.go`) systematically under-charges the true fee-market price by up to `(1e18-1)/1e18` `upc` per gas unit, multiplied by `gasUsed`. This is not a rounding artifact confined to dust — with `gasUsed` in the hundreds of thousands/millions for typical UEA payload executions, the truncated fraction accumulates into a material, deterministic, and permanent shortfall in fee revenue collected from ordinary user-submitted `MsgExecutePayload` transactions.

### Impact Explanation
This corrupts the "gas fee accounting" invariant explicitly called out in the allowed-impact list. Every unprivileged user who submits a `MsgExecutePayload` pays systematically less than the fee market's true effective price, i.e. the protocol permanently under-collects gas revenue that is supposed to be burned. This is a deterministic value leak reachable from the default transaction submission path (no privileged actor, no malicious validator/relayer needed) — the attacker is simply any ordinary UEA-routed sender. Impact is Medium: it is not a full drain, but it is a systematic, permanent loss of protocol fee revenue on every execution, and the magnitude scales with `gasUsed` and network activity.

### Likelihood Explanation
Likelihood is Medium-to-High: any block where the feemarket's multiplicative base-fee adjustment yields a `LegacyDec` value that is not an exact multiple of `1e18` (essentially every block, since EIP-1559-style adjustments are ratio-based) triggers the truncation on the very next `ExecutePayloadV2`/`ExecutePayload` call. This requires no adversarial timing — it happens by default under normal fee-market operation.

### Recommendation
Do not naively strip the `LegacyDec` scale with integer division. Either:
- Round the base fee up (ceiling) when converting to a whole-`upc` `effectiveGasPrice`, so the protocol never under-charges, or
- Preserve full `LegacyDec` precision through the entire gas-cost computation (`effectiveGasPrice * gasUsed`) and only truncate/round at the very final billed amount, using an explicit ceiling instead of truncation, so users are never systematically undercharged relative to the fee market's canonical price.

### Proof of Concept
1. Let `feemarketKeeper.GetBaseFee` return `LegacyDec` equal to `5.37 upc` (internally represented as `5370000000000000000` in 18-decimal fixed point) — a realistic value after several blocks of EIP-1559-style multiplicative adjustment.
2. `baseFeeBig := baseFee.BigInt()` → `5370000000000000000`.
3. `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` → `5` (the `0.37 upc` fraction is discarded).
4. `effectiveGasPrice = 5`, and for `gasUsed = 500,000`, `gasCost = 2,500,000 upc`.
5. The true fee-market-correct cost should be `5.37 * 500,000 = 2,685,000 upc`.
6. The user's UEA is billed and burned only `2,500,000 upc`, permanently under-paying by `185,000 upc` (≈6.9%) relative to the canonical fee-market price, with no error, no revert, and no validator/relayer collusion required — reachable via the default `MsgExecutePayload` flow.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-61)
```go
func (k Keeper) CalculateGasCost(
	baseFee sdkmath.LegacyDec,
	maxFeePerGas *big.Int,
	maxPriorityFeePerGas *big.Int,
	gasUsed uint64,
) (*big.Int, error) {
	baseFeeBig := baseFee.BigInt()
	// @dev: LegacyDec stores values with 18-decimal precision internally, so 1 upc = 1e18
	// in the LegacyDec representation. Since 1 upc is the smallest denomination (like wei
	// in Ethereum), the base fee is always a whole number of upc -- no fractional upc exists.
	// This division unwraps the LegacyDec encoding back to the actual upc amount.
	// Note: baseFee.BigInt() returns a reference to the internal big.Int; the in-place Div
	// mutates it, which is safe here since baseFee is a local value-type copy.
	baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))

```

**File:** x/uexecutor/keeper/fees.go (L72-90)
```go
	// Step 3: Find effective gas price by taking minimum
	// @dev: Currently, since we are not using maxPriorityFeePerGas, effectiveGasPrice is just baseFee
	effectiveGasPrice := new(big.Int).Set(baseFeeBig)
	// if tipPlusBase.Cmp(maxFeePerGas) == -1 {
	// 	effectiveGasPrice = tipPlusBase
	// }

	// Step 4: Calculate final gas cost: effectiveGasPrice * gasUsed
	gasUsedBig := new(big.Int).SetUint64(gasUsed)
	gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)

	k.Logger().Debug("gas cost calculated",
		"base_fee", baseFee.String(),
		"effective_gas_price", effectiveGasPrice.String(),
		"gas_used", gasUsed,
		"gas_cost", gasCost.String(),
	)

	return gasCost, nil
```

**File:** x/uexecutor/keeper/fees.go (L97-140)
```go
func (k Keeper) DeductGasFeesFromReceipt(
	ctx context.Context,
	sdkCtx sdk.Context,
	recipient common.Address,
	receipt *evmtypes.MsgEthereumTxResponse,
	universalPayload *types.UniversalPayload,
) error {
	if receipt == nil || receipt.GasUsed == 0 {
		return nil
	}
	if universalPayload == nil {
		return nil
	}

	abiPayload, err := types.NewAbiUniversalPayload(universalPayload)
	if err != nil {
		return fmt.Errorf("failed to parse payload for gas deduction: %w", err)
	}

	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}

	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}

	gasUsedBig := new(big.Int).SetUint64(receipt.GasUsed)
	if gasUsedBig.Cmp(abiPayload.GasLimit) > 0 {
		return fmt.Errorf("gas used (%d) exceeds gas limit (%s)", receipt.GasUsed, abiPayload.GasLimit.String())
	}

	recipientAccAddr := sdk.AccAddress(recipient.Bytes())
	balance := k.bankKeeper.GetBalance(sdkCtx, recipientAccAddr, pchaintypes.BaseDenom)

	if err := k.DeductAndBurnFees(ctx, recipientAccAddr, gasCost); err != nil {
		return fmt.Errorf("insufficient gas: required %s upc, available %s upc, gas_used %d, from %s: %w",
			gasCost.String(), balance.Amount.String(), receipt.GasUsed, recipient.Hex(), err)
	}
```
