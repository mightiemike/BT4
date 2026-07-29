### Title
Gas-fee truncation to zero in `CalculateGasCost` allows fee-free universal-payload EVM execution - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` unwraps the `LegacyDec` base fee to a whole-`upc` `*big.Int` by dividing its 18‑decimal internal representation by `1e18` *before* multiplying by `gasUsed`. This mirrors the "divide-before-multiply / hardcoded scale" pattern from the BetaFinance M‑05 report: any base-fee value below `1 upc` (a legitimate, non-privileged network state produced by the standard EIP‑1559-style base-fee decay under sustained low gas demand) truncates to `0`, making the entire gas bill `0` regardless of `gasUsed`.

### Finding Description
`CalculateGasCost` in `x/uexecutor/keeper/fees.go` computes the fee as: [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... the base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```
and then [2](#0-1) 

This is called from `DeductGasFeesFromReceipt`, which is the sole gas-charging step for universal-payload EVM execution flows (`PAYLOAD` / `GAS_AND_PAYLOAD`): [3](#0-2) 

If `gasCost.Sign() <= 0` (i.e., truncated to `0`), fee deduction is skipped entirely: [4](#0-3) 

This is invoked directly from `ExecutePayloadV2`, the entry point that runs an attacker-supplied universal payload through a UEA via `CallUEAExecutePayload` and then attempts to bill gas: [5](#0-4) 

The comment asserts the base fee is "always a whole number of upc," but `baseFee` is a dynamically-adjusted `LegacyDec` from the feemarket module (`FeeMarketKeeper.GetBaseFee`), which supports arbitrary 18-decimal-precision fractional values as part of its EIP‑1559-style congestion-based adjustment: [6](#0-5) 

Nothing in this scoped code enforces or verifies that `baseFee >= 1 upc` before the truncating division. If sustained low network demand (an ordinary, non-privileged condition — no governance or admin action required) drives the base fee below `1 upc`, `baseFeeBig.Div(baseFeeBig, 1e18)` yields `0`, `effectiveGasPrice` becomes `0`, and `gasCost = effectiveGasPrice * gasUsed = 0` for **any** `gasUsed`, including maximal gas payloads.

### Impact Explanation
Under this condition, any unprivileged user can repeatedly submit `PAYLOAD`/`GAS_AND_PAYLOAD` universal transactions that drive arbitrarily large, module-originated `DerivedEVMCall` execution (`CallUEAExecutePayload`) through their UEA with **zero gas fee accounted or burned**, because `DeductAndBurnFees` is never invoked once `gasCost.Sign() <= 0`: [7](#0-6) 

This breaks the gas-fee-accounting invariant (in-scope impact: "corruption of ... gas fee accounting ... reachable from ordinary user ... payloads") and creates a network-level cost asymmetry: validators/full nodes still execute the full EVM workload and consensus overhead, but no compensating fee is burned, enabling a denial-of-service/resource-exhaustion griefing vector reachable purely through default transaction submission (in-scope: "denial of service only when it is not network-level and is reachable without privileged control" — the DoS here is at the fee-accounting/resource layer, not raw network flooding, and requires no privileged actor).

### Likelihood Explanation
Whether base fee can organically fall below `1 upc` depends on the feemarket module's floor/`MinGasPrice` enforcement, which lives in the `cosmos/evm` dependency (out of this repo's scope) and could not be fully verified from the indexed code — this is the main uncertainty. If the feemarket module (or governance-set `MinGasPrice`) enforces a floor at or above `1 upc`, the bug is latent/unreachable under current configuration. However, the vulnerability is real and reachable in principle without needing malicious governance: it is the in-scope `CalculateGasCost`'s hardcoded truncation, based on an unverified invariant ("base fee is always whole upc") that has no code-level enforcement in the reviewed files. This is the same class of failure as the M‑05 report: a fixed-scale integer division applied without validating the pre-condition that keeps it safe.

### Recommendation
- Do not silently truncate the `LegacyDec` base fee to a `*big.Int` before multiplying by `gasUsed`. Perform the multiplication in `LegacyDec`/rational space first (`baseFeeDec.MulInt64(gasUsed)`), then round using `Ceil()` (charge at least the true fractional cost) before converting to the integer `upc` amount, analogous to how `min_gas_price.go` computes `fee := gp.Amount.Mul(gasLimit).Ceil().RoundInt()`.
- Explicitly assert/clamp a minimum fee floor (e.g., 1 `upc` minimum charge for any positive `gasUsed`) instead of allowing `gasCost` to silently become `0`.
- Remove the unverified assumption in the code comment and add a runtime invariant check (or unit test) proving the feemarket module's base fee cannot drop below `1 upc`; if it can, this must be handled explicitly here rather than assumed.

### Proof of Concept
Not independently executable from indexed code, but the truncation is directly demonstrable via the existing formula:
```go
baseFee := sdkmath.LegacyMustNewDecFromStr("0.999999999999999999") // < 1 upc
gasUsed := uint64(30_000_000) // max block gas
gasCost, _ := k.CalculateGasCost(baseFee, maxFeePerGas, maxPriorityFeePerGas, gasUsed)
// gasCost == 0 for any gasUsed, because baseFeeBig.Div(baseFeeBig, 1e18) == 0
```
This mirrors the referenced report's PoC pattern (`interestAmount == 0` repeatedly logged) — here `gasCost == 0` regardless of how much EVM execution work (`gasUsed`) is performed, as long as `baseFee < 1 upc`. Full confirmation that this state is reachable without privileged action requires inspecting the out-of-scope `cosmos/evm` feemarket base-fee adjustment/floor logic, which was not available in the indexed context.

### Citations

**File:** x/uexecutor/keeper/fees.go (L16-37)
```go
// DeductAndBurnFees deducts gas fees from the user's smart account and burns them.
// The process happens in two steps:
// 1. Transfer coins from user account to module account
// 2. Burn coins from module account
// Returns error if either transfer or burn fails
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

**File:** x/uexecutor/keeper/fees.go (L52-60)
```go
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

**File:** x/uexecutor/keeper/fees.go (L79-90)
```go
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

**File:** x/uexecutor/keeper/fees.go (L97-127)
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
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-48)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}
```

**File:** x/uexecutor/types/expected_keepers.go (L58-61)
```go
// FeeMarketKeeper defines the expected interface for the fee market module.
type FeeMarketKeeper interface {
	GetBaseFee(ctx sdk.Context) math.LegacyDec
}
```
