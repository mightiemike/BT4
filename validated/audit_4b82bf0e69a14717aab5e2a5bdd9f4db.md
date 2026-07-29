## Title
Integer truncation of `baseFee` in `CalculateGasCost` nullifies gas-fee accounting once `baseFee` drops below 1 `upc` - (File: `x/uexecutor/keeper/fees.go`)

## Summary
`x/uexecutor/keeper/fees.go`'s `CalculateGasCost` truncates the fee-market `baseFee` (`sdkmath.LegacyDec`) by dividing its raw internal `big.Int` representation by `1e18` before using it as the effective gas price. This mirrors the SophonFarming `pointsPerBlock` issue: a value that must retain 1e18 fixed-point precision through a division is instead prematurely collapsed to a whole-unit integer, and once the true value is smaller than one whole unit the division truncates to zero, nullifying the entire computation downstream.

## Finding Description
`CalculateGasCost` does: [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// ... comment claims baseFee is "always a whole number of upc"
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

`baseFee` is an `sdkmath.LegacyDec` sourced from `x/feemarket`'s `GetBaseFee` [2](#0-1) , an EIP-1559-style value that is adjusted up/down every block based on gas utilization. `LegacyDec.BigInt()` returns the *raw* fixed-point representation (`actual_value * 1e18`), and the code assumes `actual_value` (the base fee expressed in whole `upc`) is always `>= 1`, so dividing the raw representation by `1e18` "unwraps" it back to a whole-`upc` integer. This assumption is not enforced anywhere in the feemarket parameters or in this function: nothing prevents the dynamically-adjusted `baseFee` from decaying to a value less than 1 `upc` (e.g., after a sustained run of low-utilization blocks, since EIP-1559 base fee decreases geometrically toward zero and genesis configs only set an initial value, not a floor). Once `baseFee < 1 upc`, `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates to `0`, making `effectiveGasPrice = 0` and therefore `gasCost = effectiveGasPrice * gasUsed = 0` regardless of how much gas was actually consumed.

This feeds directly into `DeductGasFeesFromReceipt`, which is called from the universal-execution payload path (`x/uexecutor/keeper/execute_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`, `msg_execute_payload.go`) to charge the UEA/recipient account for gas consumed by a `DerivedEVMCall`-executed payload: [3](#0-2) 

```go
gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
...
if gasCost.Sign() <= 0 {
    return nil
}
```

When `gasCost` truncates to zero, the function returns `nil` and silently skips `DeductAndBurnFees` — the account is never charged despite the module having produced a real EVM execution with non-zero `gasUsed`. This is an unprivileged, attacker-reachable path: any user submitting `MsgExecutePayload` (or any inbound with a payload) benefits from unlimited free EVM-payload execution once the ambient base fee decays under 1 `upc`, with the protocol/module perpetually absorbing the real cost without recouping it — a corruption of the gas-fee accounting invariant analogous to the referenced `accPointsPerShare` nullification.

## Impact Explanation
This falls under "corruption of ... gas fee accounting" in the allowed impact gate. Once `baseFee` is (or becomes) sub-1-`upc`, every UEA/CEA payload execution routed through `DeductGasFeesFromReceipt` bypasses fee collection entirely while the module still performs and pays for the underlying `DerivedEVMCall`. This is a systemic, repeatable loss (not a one-off): any unprivileged user can drive unlimited free payload executions during any period where the dynamically-adjusted base fee sits below 1 `upc`, extracting sustained value from protocol-subsidized execution with zero accounting cost recorded against them.

## Likelihood Explanation
The base fee is not attacker-set, but it is a live, dynamically-adjusted parameter (`x/feemarket`, adjusted per EIP-1559 rules) that decreases block by block during periods of low chain utilization. There is no enforced floor keeping it above `1e18` (1 whole `upc`) in the codebase reviewed. Reaching this state does not require any privileged action — it results from ordinary network conditions (e.g., low activity periods on a young or quiet chain) — and is a deterministic function of `CalculateGasCost`'s division order, so it is highly likely to be reachable over the life of the chain rather than a purely theoretical edge case.

## Recommendation
Do not divide the `LegacyDec`'s raw fixed-point representation directly. Instead perform the gas-cost multiplication in `LegacyDec` space (or with a higher-precision intermediate) and only convert to a whole-`upc` integer amount after multiplying by `gasUsed`, e.g.:
```go
gasCostDec := baseFee.MulInt64(int64(gasUsed))   // still 1e18-precision
gasCost := gasCostDec.Quo(sdkmath.LegacyNewDec(1e18)).TruncateInt().BigInt()
```
or equivalently keep `baseFee` as a `Dec` through the whole computation and only floor/ceil once, after multiplying by `gasUsed`, so fractional-`upc` base fees still yield a non-zero charge once accumulated across enough gas units. Alternatively, enforce a hard minimum base fee (analogous to enforcing the `1e18` precision floor recommended in the original report) so `baseFee` can never fall below `1 upc` in the feemarket params.

## Proof of Concept
1. Let `x/feemarket` base fee decay (through normal EIP-1559-style adjustment over several low-utilization blocks) to a value below `1e18` in its raw internal representation, e.g. `baseFee = 0.5 upc` (raw big.Int `5*10^17`).
2. Any user submits `MsgExecutePayload` (or an inbound with a payload) that triggers a `DerivedEVMCall` consuming, say, `gasUsed = 1,000,000`.
3. `CalculateGasCost` computes `baseFeeBig.Div(5*10^17, 1e18) = 0`, so `effectiveGasPrice = 0`, `gasCost = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without calling `DeductAndBurnFees` — the recipient account is never charged for the executed gas, even though the module paid for a real `1,000,000`-gas EVM execution. [4](#0-3)

### Citations

**File:** x/uexecutor/keeper/fees.go (L39-91)
```go
// CalculateGasCost calculates the gas cost based on EIP-1559 fee mechanism:
// 1. Effective Gas Price = min(maxFeePerGas, baseFee + maxPriorityFeePerGas)
// 2. Total Fee = gasUsed × Effective Gas Price
// Parameters:
// - baseFee: current network base fee
// - maxFeePerGas: maximum total fee user is willing to pay
// - maxPriorityFeePerGas: maximum tip to validator
// - gasUsed: amount of gas consumed
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

	// Step 1: Validate maxFeePerGas >= baseFee
	if maxFeePerGas.Cmp(baseFeeBig) < 0 {
		return nil, fmt.Errorf("maxFeePerGas (%s) cannot be less than baseFee (%s)", maxFeePerGas, baseFeeBig)
	}

	// Step 2: Calculate baseFee + maxPriorityFeePerGas (potential effective gas price)
	// @dev: Currently, we are not using maxPriorityFeePerGas in the calculation
	// tipPlusBase := new(big.Int).Add(baseFeeBig, maxPriorityFeePerGas)
	// tipPlusBase := maxFeePerGas

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
}
```

**File:** x/uexecutor/keeper/fees.go (L121-127)
```go
	gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
	if err != nil {
		return fmt.Errorf("failed to calculate gas cost: %w", err)
	}
	if gasCost.Sign() <= 0 {
		return nil
	}
```

**File:** x/uexecutor/types/expected_keepers.go (L58-61)
```go
// FeeMarketKeeper defines the expected interface for the fee market module.
type FeeMarketKeeper interface {
	GetBaseFee(ctx sdk.Context) math.LegacyDec
}
```
