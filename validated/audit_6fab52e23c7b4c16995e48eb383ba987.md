### Title
Truncating integer division in `CalculateGasCost` silently undercharges gas fees, causing systemic gas-fee-accounting loss - ([File: x/uexecutor/keeper/fees.go])

### Summary
`Keeper.CalculateGasCost` in [1](#0-0)  converts the feemarket's `LegacyDec` base fee back into a whole-`upc` integer using a plain truncating integer division (`baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))`), on the documented but unverified assumption that "the base fee is always a whole number of upc — no fractional upc exists." This is the same bug class as the reported `ImpossibleLibrary.sol` issue: a value carrying extra fixed-point precision (`LegacyDec`, 18-decimal internal scale) is combined with a lower-precision integer operation without properly rounding, silently discarding the fractional remainder and losing accuracy in a fee/accounting computation.

### Finding Description
`CalculateGasCost` is the sole source of the "effective gas price" used to compute how much native `upc` is deducted (and burned) from a UEA/recipient account after every `executeUniversalTx` / `ExecutePayload` / `ExecutePayloadV2` call: [2](#0-1) 

- `baseFee` is retrieved from `k.feemarketKeeper.GetBaseFee(sdkCtx)` as an `sdkmath.LegacyDec`, i.e. a value with 18 decimal digits of fixed-point precision [3](#0-2) .
- The code assumes the *real* base-fee value is always an integer number of `upc` and unwraps the `LegacyDec` encoding with `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` — ordinary integer division that floors any remainder rather than rounding.
- Cosmos EVM's feemarket `base_fee` is adjusted every block by a proportional formula based on the gas-used ratio (EIP-1559-style elasticity adjustment). Such adjustments are fractional multiplications of the previous base fee, so the resulting `LegacyDec` value is not guaranteed to be an exact multiple of `1e18` at every block — the assumption embedded in the comment is not actually enforced anywhere in the code.
- Whenever the internal `LegacyDec` value is not cleanly divisible by `1e18`, the truncating `Div` call silently drops the sub-`upc` remainder, so `effectiveGasPrice` (and hence `gasCost = effectiveGasPrice * gasUsed`) is computed strictly lower than the true consensus-determined gas price.
- `gasCost` then flows directly into `DeductAndBurnFees`, which transfers and burns exactly that (undercounted) amount from the payload sender/recipient's balance [4](#0-3) .

This computation is executed on every unprivileged, user-triggered payload execution path (`ExecutePayload`, `ExecuteInboundFundsAndPayload`, `ExecuteInboundGasAndPayload`) via `DeductGasFeesFromReceipt` [5](#0-4) [6](#0-5) , so any ordinary user submitting a UniversalPayload transaction triggers the miscalculation with no special privileges required.

### Impact Explanation
This falls under "corruption of ... gas fee accounting" in the allowed impact set. Every payload execution whose base fee at that block is not an exact multiple of `1e18` upc has its gas cost floored, meaning the module burns strictly less `upc` than the network's own EIP-1559 base fee dictates. Because this executes unconditionally on the honest/default code path (not an edge case requiring attacker manipulation), it is a systemic, protocol-wide undercollection of gas fees rather than a one-off rounding artifact — over time this represents a persistent leak of protocol fee revenue that the accounting invariant "gas cost == effective gas price × gas used" is supposed to guarantee.

### Likelihood Explanation
High likelihood of occurrence: base fee adjustment under the standard EIP-1559-style algorithm used by Cosmos EVM's feemarket module is computed via proportional (percentage-based) block-to-block adjustments, which routinely produce values that are not exact multiples of `10^18` in the underlying `LegacyDec` representation. No unusual attacker action is required — any normal payload transaction executed while the base fee happens to carry a fractional `upc` component reproduces the loss.

### Recommendation
Do not silently truncate the `LegacyDec` base fee back to an integer. Either:
1. Perform the whole gas-cost computation directly in `LegacyDec` precision and only round/ceil once at the very end (e.g., `gasCost = baseFeeDec.MulInt64(gasUsed).Ceil().RoundInt()`), consistent with how `min_gas_price.go` computes required fees using `Ceil().RoundInt()` [7](#0-6) ; or
2. If integer base-fee semantics are truly required, use rounding (round-half-up or ceil) instead of floor division, and assert/validate that the remainder is zero rather than silently discarding it — failing loudly if the invariant "base fee is a whole upc" is ever violated, instead of quietly under-charging.

### Proof of Concept
1. Set feemarket base fee to a `LegacyDec` value that is not an exact multiple of `1e18`, e.g. `1_000_000_000_500_000_000` (1.0000000005 upc-equivalent) — a value reachable via normal per-block EIP-1559 adjustment when the previous block's base fee was itself non-round and the gas-used ratio produced a non-integer multiplier.
2. Submit an ordinary `MsgExecutePayload` / inbound with a `UniversalPayload` whose `maxFeePerGas` covers this base fee.
3. `CalculateGasCost` computes `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` → truncates to `1_000_000_000` (losing the `500_000_000` remainder, i.e. 0.5 upc-equivalent scaled by `gasUsed`).
4. `DeductAndBurnFees` burns `effectiveGasPrice * gasUsed` using the truncated `effectiveGasPrice`, permanently under-collecting `(true baseFee − truncated baseFee) * gasUsed` in `upc` for every affected transaction, with no error or invariant check raised anywhere in the call path.

### Citations

**File:** x/uexecutor/keeper/fees.go (L21-37)
```go
func (k Keeper) DeductAndBurnFees(ctx context.Context, from sdk.AccAddress, gasCost *big.Int) error {
	amt := sdkmath.NewIntFromBigInt(gasCost)
	coin := sdk.NewCoin(pchaintypes.BaseDenom, amt)

	k.Logger().Debug("deducting and burning fees",
		"from", from.String(),
		"gas_cost", gasCost.String(),
		"denom", pchaintypes.BaseDenom,
	)

	err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, from, types.ModuleName, sdk.NewCoins(coin))
	if err != nil {
		return err
	}

	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(coin))
}
```

**File:** x/uexecutor/keeper/fees.go (L47-81)
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
```

**File:** x/uexecutor/keeper/fees.go (L116-119)
```go
	baseFee := k.feemarketKeeper.GetBaseFee(sdkCtx)
	if baseFee.IsNil() {
		return fmt.Errorf("base fee not found")
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L89-93)
```go
	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L250-255)
```go
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
```

**File:** app/cosmos/min_gas_price.go (L70-79)
```go
	// Determine the required fees by multiplying each required minimum gas
	// price by the gas limit, where fee = ceil(minGasPrice * gasLimit).
	gasLimit := math.LegacyNewDecFromBigInt(new(big.Int).SetUint64(gas))

	for _, gp := range minGasPrices {
		fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()
		if fee.IsPositive() {
			requiredFees = requiredFees.Add(sdk.Coin{Denom: gp.Denom, Amount: fee})
		}
	}
```
