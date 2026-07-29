## Title
Integer-Division Truncation in `CalculateGasCost` Allows Zero-Fee UEA/CEA Payload Execution When Base Fee Falls Below 1 `upc` - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`x/uexecutor/keeper/fees.go`'s `CalculateGasCost` converts the fee market's `LegacyDec` base fee into a whole-`upc` `big.Int` via integer division by `1e18` before computing the gas cost to charge a UEA/CEA account for module-originated EVM execution. The code's own comment assumes "the base fee is always a whole number of upc," but nothing in the fee-market's dynamic EIP-1559-style adjustment enforces that invariant. If the on-chain base fee ever decays below `1e18` (1 `upc`) — which is a normal, reachable outcome of sustained low/zero gas usage under the standard base-fee-decrease mechanism — the division truncates to zero, `effectiveGasPrice` becomes `0`, and the resulting `gasCost` is `0` regardless of how much real EVM gas was actually consumed.

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

`baseFee.BigInt()` returns the raw fixed-point representation of the `LegacyDec` (18-decimal precision), and dividing by `1e18` is only lossless when the decimal value is an exact integer number of `upc`. There is no validation anywhere in this path that guarantees `baseFee ∈ ℤ`; the fee market computes base fee dynamically from block gas utilization and can produce arbitrarily small fractional values over consecutive low-usage blocks. Once `baseFeeBig < 1e18`, the `Div` call (Go's `big.Int` division truncates toward zero) yields `0`.

Downstream, `DeductGasFeesFromReceipt` treats a non-positive `gasCost` as "nothing to charge": [2](#0-1) 

```go
gasCost, err := k.CalculateGasCost(baseFee, abiPayload.MaxFeePerGas, abiPayload.MaxPriorityFeePerGas, receipt.GasUsed)
...
if gasCost.Sign() <= 0 {
    return nil
}
```

This is called from the `MsgExecutePayload` / inbound-payload execution flow after real EVM computation has already been performed via `DerivedEVMCall` against the recipient's UEA. So the EVM state transition (arbitrary contract execution, gas-metered by the fork) is fully committed, but the "accounting" step that is supposed to recover that cost from the executing account's `upc` balance silently no-ops.

### Impact Explanation
This corrupts gas-fee accounting for module-originated EVM execution (`CallUEAExecutePayload` / `CallExecuteUniversalTx` paths) — an explicitly in-scope impact ("corruption of ... gas fee accounting ... reachable from ordinary user deposits, payloads, contracts, or default transaction submission paths alone"). Any unprivileged user submitting `MsgExecutePayload` (a gasless-at-the-Cosmos-tx-level message per `app/txpolicy/gasless.go`, whose *real* cost is supposed to be recovered via this exact `upc` deduction from the UEA) gets free, unmetered EVM computation whenever the network's base fee has decayed under 1 `upc`. Since `MsgExecutePayload` costs the submitter nothing at the Cosmos ante-handler layer, this is the *only* place the protocol recovers compute cost — losing it here means the execution is entirely free to the attacker while consuming real validator resources, with no economic backstop.

### Likelihood Explanation
The trigger condition (`baseFee < 1 upc`) requires no privileged action — it is a natural steady-state outcome of the fee market's EIP-1559-style base-fee-decrease algorithm during periods of low network utilization, which any external, unprivileged party can simply wait for (or help induce by not generating gas usage). Once the condition holds, any ordinary user can submit a normal `MsgExecutePayload` through the standard submission path to receive undercharged (zero-cost) execution. No malicious validator, TSS participant, or admin action is required.

### Recommendation
Do not assume `baseFee` is always a whole number of `upc`. Round up (ceiling) rather than truncate when converting the `LegacyDec` base fee to a whole-token amount for billing purposes (mirroring the `Ceil().RoundInt()` pattern already used correctly in `app/cosmos/min_gas_price.go`), or compute `gasCost` directly in the full 18-decimal `LegacyDec` domain and only convert/round at the very end. At minimum, treat any non-zero `gasUsed` with a non-zero `baseFee` (even sub-`upc`) as requiring a minimum charge of `1 upc`, so genuine EVM computation is never accounted as free.

### Proof of Concept
1. Let the network run through several blocks with `no_base_fee=false` and low/zero gas utilization until the fee-market's dynamic adjustment drives `feemarket.params.base_fee` (or the tracked current base fee) below `1.000000000000000000` (i.e., below 1 `upc`), e.g. `0.5`.
2. An unprivileged user submits a normal `MsgExecutePayload` (or any inbound triggering `CallUEAExecutePayload`/`CallExecuteUniversalTx`) whose EVM execution consumes non-trivial `gasUsed` (e.g. a large contract call) and specifies `maxFeePerGas >= 1` so `CalculateGasCost`'s only guard (`maxFeePerGas.Cmp(baseFeeBig) < 0`) passes trivially (`baseFeeBig` is already `0` after truncation).
3. `CalculateGasCost` computes `baseFeeBig.Div(0.5e18, 1e18) == 0`, so `effectiveGasPrice = 0` and `gasCost = 0 * gasUsed = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without ever calling `DeductAndBurnFees` — the executing UEA's `upc` balance is untouched despite genuine EVM computation having occurred.

### Citations

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
