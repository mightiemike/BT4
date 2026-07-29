### Title
Gas-cost truncation in `CalculateGasCost` lets UEA payload senders systematically underpay/under-burn gas fees - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` derives the whole-`upc` base fee from a `sdkmath.LegacyDec` by dividing its internal 18-decimal-scaled `*big.Int` representation by `1e18`, discarding any fractional remainder instead of rounding up. This is the same class of bug as the reported `_calculateInterest` issue: a division that truncates toward zero, silently producing a smaller-than-correct value that is then used to compute the amount a user must pay (here, gas fee burned via `DeductGasFeesFromReceipt` → `DeductAndBurnFees`), rather than the amount a protocol invariant requires.

### Finding Description
`k.CalculateGasCost` at [1](#0-0)  does:

```go
baseFeeBig := baseFee.BigInt()
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

`baseFee` is an `sdkmath.LegacyDec` obtained from `k.feemarketKeeper.GetBaseFee(sdkCtx)` [2](#0-1) . `LegacyDec` stores an 18-decimal fixed-point number as a raw `*big.Int` (value × 1e18). The code's own comment asserts "no fractional upc exists," but the cosmos-evm `feemarket` module's dynamic base-fee (AIMD-style) adjustment is designed to retain sub-unit precision across blocks specifically to avoid drift — the accumulated base fee is not guaranteed to be an exact multiple of `1e18` at any given block. When it isn't, `baseFeeBig.Div(...)` performs an integer (floor) division, truncating the fractional `upc` component instead of rounding up.

That truncated `effectiveGasPrice` is then multiplied by `gasUsed` to compute `gasCost` [3](#0-2) , which is burned from the UEA-derived account via `DeductAndBurnFees` inside `DeductGasFeesFromReceipt` [4](#0-3) . This path is reached on every ordinary user `MsgExecutePayload` call through `ExecutePayload` [5](#0-4)  — a fully unprivileged, user-reachable flow.

Because the per-unit truncation (up to just under 1 `upc`) is multiplied by `gasUsed` (which can be large, e.g. tens of millions per the test payloads that set `GasLimit: "21000000"`), the aggregate underpayment per transaction can be non-trivial and is fully deterministic/predictable from the current base fee, not a one-off rounding artifact.

### Impact Explanation
This corrupts gas-fee/burn accounting (an explicitly in-scope impact category: "corruption of ... gas fee accounting"). Every payload execution burns strictly less than the fee-market-implied cost whenever the base fee carries a fractional `upc` component, meaning the protocol permanently under-collects the fee it is supposed to burn from users on the universal execution path. This is a systemic, repeatable value leak rather than a one-time edge case, and it is triggerable by any ordinary UEA user simply by executing payloads while the network base fee happens to be non-integer (a state the fee market can reach continuously under normal usage).

### Likelihood Explanation
Likelihood depends on how often `GetBaseFee` returns a `LegacyDec` value that isn't an exact multiple of `1e18`. This is plausible under cosmos-evm's standard EIP-1559-like fee market, which updates the base fee gradually as a `Dec` to avoid oscillation/drift, but I could not fully verify the exact base-fee update formula in this repository (the `feemarket` module is an external cosmos-evm dependency, not vendored/directly inspectable here). If the underlying feemarket module always rounds/truncates the stored `BaseFee` to a whole `upc` itself before persisting it, this `Div` would be a genuine no-op and the finding would not be exploitable in practice — this could not be conclusively ruled in or out with the available tools.

### Recommendation
- Round the base fee up (ceiling) rather than truncating: use `baseFee.Ceil().TruncateInt()` (mirroring the existing `ceil` pattern already used elsewhere in `checkTxFeeWithValidatorMinGasPrices` for min-gas-price fee calc: `fee.Ceil().RoundInt()`), so the protocol never systematically undercharges.
- Alternatively/additionally, confirm and document (with a unit test) that `feemarketKeeper.GetBaseFee` is guaranteed to always return values that are exact multiples of `1e18`; if that invariant cannot be guaranteed upstream, the rounding must be handled defensively at the call site in `x/uexecutor`.

### Proof of Concept
Could not be constructed with certainty within the available tools, because triggering the bug requires forcing `feemarketKeeper.GetBaseFee` to return a fractional `LegacyDec` (e.g., `12345.5`), and the exact base-fee update algorithm used by the vendored `feemarket` module was not directly inspectable in this session. Conceptually:
1. Drive network gas usage (via ordinary `MsgExecutePayload`/EVM transactions) so the feemarket's AIMD adjustment produces a `BaseFee` such as `12345500000000000000000` (i.e., `12345.5 * 1e18`) internally.
2. Submit a `MsgExecutePayload` with a large `GasLimit`/`gasUsed`.
3. Observe that `CalculateGasCost` computes `effectiveGasPrice = 12345` (floor) instead of `12345.5`, and the resulting `gasCost` burned via `DeductAndBurnFees` is `(12345.5 - 12345) * gasUsed` `upc` short of the fee-market-correct amount. [6](#0-5)

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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L86-93)
```go
	// Step 3: Execute payload through UEA
	receipt, execErr := k.CallUEAExecutePayload(sdkCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 4: Deduct gas fees regardless of success/failure.
	// If deduction fails, return error so the entire Cosmos tx rolls back (including EVM state).
	if feeErr := k.DeductGasFeesFromReceipt(ctx, sdkCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		return fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}
```
