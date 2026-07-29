## Finding: Base-fee truncation in `DeductGasFeesFromReceipt` zeroes out gas billing for `MsgExecutePayload` execution

### Title
Integer-truncation of `baseFee` in gas cost calculation lets UEA payload execution go unbilled - (File: `x/uexecutor/keeper/fees.go`)

### Summary
The Optimism M-5 bug is a class of "gas-limit/gas-price arithmetic lets the caller consume execution resources while paying zero compensation." Push Chain's analog invariant is: **every unit of gas a `UniversalPayload` consumes on the UEA must be billed to the owning UEA** via `DeductGasFeesFromReceipt`/`CalculateGasCost` in [1](#0-0) . Instead of billing off the pre-declared `gasLimit` (like Optimism did), Push bills off the actual `receipt.GasUsed`, which closes the exact Optimism vector — but a separate truncation bug in the price calculation reproduces the same end effect: zero-cost L2 execution.

### Finding Description
`CalculateGasCost` converts the feemarket's `baseFee` (an 18-decimal `sdkmath.LegacyDec`) to a whole-`upc` `big.Int` by integer-dividing the raw internal representation by `1e18`: [2](#0-1) 

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
...
if maxFeePerGas.Cmp(baseFeeBig) < 0 { ... }
```

The comment claims "the base fee is always a whole number of upc," but nothing in this function or its callers enforces or validates that assumption against the feemarket's actual `MinGasPrice`/base-fee scale. Standard EIP-1559 base fees (and cosmos-evm/feemarket defaults) are denominated per unit of gas and are typically tiny fractions of one whole native token (e.g. sub-`1e18` raw units) — not "1 or more upc per gas." Whenever `baseFee < 1e18` (in the `LegacyDec` raw units, i.e. less than one whole `upc` per gas), `baseFeeBig.Div(..., 1e18)` truncates to `0`. That zero then flows straight through: [3](#0-2) 

```go
effectiveGasPrice := new(big.Int).Set(baseFeeBig)   // == 0
...
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)  // == 0
```

Back in the caller: [4](#0-3) 

```go
gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
...
if gasCost.Sign() <= 0 {
    return nil   // no fee, no burn, silently succeeds
}
```

The result: `receipt.GasUsed` can be arbitrarily large (attacker crafts an expensive `to`/`data` payload through `MsgExecutePayload`), but `DeductGasFeesFromReceipt` — reached from `ExecutePayload`/`ExecutePayloadV2` for both direct user calls and inbound-driven execution — silently skips billing whenever the truncated `baseFeeBig` is `0`, i.e. whenever the real base fee is anything less than one whole `upc` per gas unit. This is functionally identical to the Optimism M-5 root cause: an arithmetic short-circuit in the gas-price/gas-limit relationship lets a caller consume real execution resources (EVM computation billed against the sequencer/validators) while the accounting layer computes a zero charge.

### Impact Explanation
If exploitable, any unprivileged user who can submit `MsgExecutePayload` (a gasless message — the Cosmos-level fee is already waived, see `app/txpolicy/gasless.go`) could execute unlimited EVM computation through their UEA at zero cost whenever the network's real base fee sits below the truncation threshold, which is the common EIP-1559 regime for a low-traffic chain. This corrupts gas fee/refund accounting and lets users consume core-validator/UEA execution resources without compensation — falling under "corruption of ... gas fee accounting" and "using L2 resources without enough compensation" in the allowed impact gate, and it can be repeated as a DoS vector (spam expensive payloads for free).

### Likelihood Explanation
Medium-to-High, but **unverified**: I could not confirm the concrete numeric scale that `feemarketKeeper.GetBaseFee` returns in this codebase (i.e., whether the deployed feemarket parameters guarantee `baseFee ≥ 1e18` raw units in practice, which would make the `Div(..., 1e18)` truncation a no-op under realistic conditions). The function's own comment asserts baseFee is "always a whole number of upc," which if actually enforced elsewhere (e.g., via `MinGasPrice` params permanently set ≥ 1 upc) would neutralize this issue. I was not able to locate that enforcement within the reachable index (feemarket module internals and default `genesis.json`/params for `MinGasPrice` were not found). This should be confirmed against the live/deployed feemarket parameters before treating this as an active vulnerability.

### Recommendation
- Do not integer-truncate `baseFee` to whole `upc`; keep the calculation in the same fractional/atto-`upc` (wei-equivalent) precision that the EVM and feemarket module actually use for gas pricing, matching however `MaxFeePerGas`/`MaxPriorityFeePerGas` are denominated.
- Add an explicit invariant check/test asserting `gasCost > 0` whenever `receipt.GasUsed > 0` and `baseFee > 0`, and fail loudly (rather than silently returning `nil`) if the computed fee would be zero for nonzero gas usage.
- Confirm and pin down the actual units/scale of `feemarketKeeper.GetBaseFee` in this codebase and add a unit test with a realistic sub-1-upc base fee (e.g. matching a plausible `MinGasPrice` from `app.toml`/genesis) to lock in that gas is still billed.

### Proof of Concept
Not independently confirmed end-to-end due to inability to verify the feemarket's real base-fee scale from the available index; the arithmetic path is a straightforward Go-level PoC:
1. Set `baseFee` (a `sdkmath.LegacyDec`) to any value whose raw internal representation is `< 1e18` (i.e., less than 1 whole `upc` per gas — the normal case for any EIP-1559 base fee expressed in the finer-grained units gas prices are usually quoted in).
2. Call `k.CalculateGasCost(baseFee, maxFeePerGas, maxPriorityFeePerGas, gasUsed)` with any `gasUsed > 0`.
3. Observe `baseFeeBig` truncates to `0`, `effectiveGasPrice = 0`, `gasCost = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without deducting/burning any fee, even though `receipt.GasUsed` (the real EVM execution work) was nonzero.

Given the residual uncertainty flagged above, this should be validated against the actual deployed feemarket parameters (min base fee floor) before being treated as a confirmed exploitable finding.

### Citations

**File:** x/uexecutor/keeper/fees.go (L52-65)
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

	// Step 1: Validate maxFeePerGas >= baseFee
	if maxFeePerGas.Cmp(baseFeeBig) < 0 {
		return nil, fmt.Errorf("maxFeePerGas (%s) cannot be less than baseFee (%s)", maxFeePerGas, baseFeeBig)
	}
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

**File:** x/uexecutor/keeper/fees.go (L93-148)
```go
// DeductGasFeesFromReceipt calculates and deducts gas fees from a recipient address
// based on the EVM receipt and universal payload parameters.
// Returns nil if receipt is nil (Go-level error, no EVM tx was created).
// Returns error with gas details if deduction fails (insufficient balance, etc).
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

	k.Logger().Debug("gas fees deducted",
		"recipient", recipient.Hex(),
		"gas_used", receipt.GasUsed,
		"gas_cost", gasCost.String(),
	)
	return nil
}
```
