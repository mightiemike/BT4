I have enough evidence to write up the finding. The `feemarket` `BaseFee` is a `LegacyDec` (18-decimal fixed point) whose value is set by the standard cosmos-evm EIP-1559-style dynamic adjustment algorithm, which multiplies the previous base fee by a continuous ratio every block — it is **not** constrained to be a whole-`upc` integer. `x/uexecutor`'s `CalculateGasCost` assumes the opposite and truncates via integer division, silently zeroing the fee whenever `baseFee` decays below 1 `upc` (a realistic outcome after a sustained run of underfull blocks).

### Title
Gas-fee truncation to zero for `MsgExecutePayload` when the dynamic feemarket `BaseFee` decays below 1 `upc` - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` in [1](#0-0) , based on the comment's assumption that "the base fee is always a whole number of upc — no fractional upc exists". This assumption is not enforced anywhere in scope: the dynamic `feemarket` base fee is a continuously-adjusted `LegacyDec` that can legitimately fall below `1e18` (i.e. below 1 whole `upc`) while remaining non-zero, e.g. after a sustained run of blocks under the gas target. When that happens, integer division truncates `baseFeeBig` to `0`, making `effectiveGasPrice = 0` and thus `gasCost = 0` regardless of `gasUsed`, exactly mirroring the SpeedJumpIrm bug class: a genuinely non-zero divisor/value collapses to the same output as the explicit zero-case, silently corrupting the computed value instead of raising an error or falling back to a safe minimum.

### Finding Description
`DeductGasFeesFromReceipt` [2](#0-1) . This is the sole gas-accounting path for `MsgExecutePayload` (the gasless UEA-payload-execution message documented in the module's own README as burning gas from the UEA's balance instead of a Cosmos tx fee) [3](#0-2) . Because `MsgExecutePayload` is on the gasless allowlist, the Cosmos-level `MinGasPriceDecorator`/`DeductFeeDecorator` never charge anything either [4](#0-3)  — `DeductGasFeesFromReceipt` is the *only* place gas is actually billed for this flow. When `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates a small-but-nonzero base fee to `0`, an attacker (any unprivileged sender of `MsgExecutePayload`) executes real EVM work (deposit/payload calls through their UEA) while paying zero gas, silently under-charging/avoiding fee burn — corrupting gas-fee accounting even though real `gasUsed` was consumed by the EVM.

### Impact Explanation
This falls under "corruption of ... gas fee accounting" in the allowed-impact list. Fees intended to be burned are instead never collected once the dynamic base fee dips under 1 `upc`, letting ordinary users execute UEA payloads for free indefinitely at that base-fee level, undermining the protocol's fee-burn/anti-spam economics for the gasless execution path.

### Likelihood Explanation
Requires no privileged actor — any account can submit `MsgExecutePayload`. The trigger condition (base fee decaying under `1e18` in the `LegacyDec` representation) depends on network congestion history and the deployed feemarket parameters (genesis configs in this repo default base fee/min-gas-price to large integers like `1e6`–`1e9` `upc`, which would need many low-usage blocks of multiplicative decay to fall under `1 upc`), so likelihood depends on production feemarket parameterization and elasticity settings rather than being instantly reachable at genesis.

### Recommendation
Do not floor-divide the `LegacyDec` base fee to an integer `upc` count. Instead, compute `gasCost` using full `LegacyDec` precision (e.g. `baseFee.MulInt64(int64(gasUsed))`) and round up (`Ceil`) before converting to `*big.Int`, so any non-zero base fee always yields a non-zero gas cost proportional to `gasUsed`, matching the fix pattern used elsewhere in this codebase for `ceil(minGasPrice * gasLimit)` [5](#0-4) .

### Proof of Concept
1. Let `feemarket` base fee decay (via the standard cosmos-evm AIMD adjustment across successive underfull blocks) until `GetBaseFee(ctx)` returns a `LegacyDec` value `> 0` but `< 1e18` (e.g. `0.5`).
2. Attacker submits `MsgExecutePayload` for their UEA with a payload that consumes non-trivial `gasUsed` (e.g. a deposit/swap call).
3. `CalculateGasCost` computes `baseFeeBig = 5e17`, then `baseFeeBig.Div(baseFeeBig, 1e18) = 0` [6](#0-5) , giving `effectiveGasPrice = 0` and `gasCost = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without deducting/burning any `upc` from the UEA [7](#0-6) , even though `receipt.GasUsed > 0` real EVM gas was consumed.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-60)
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

**File:** x/uexecutor/README.md (L216-216)
```markdown
- **`UniversalAccountId.Owner`** identifies the UEA whose pre-authorized payload is being executed. The actual EVM execution gas is deducted from this UEA;s balance (`DeductGasFeesFromReceipt`), not from the signer.
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

**File:** app/cosmos/min_gas_price.go (L81-84)
```go
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		return next(ctx, tx, simulate)
	}
```
