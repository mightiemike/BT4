### Title
Premature division in `CalculateGasCost` truncates gas fee to zero when the base fee is sub-1-upc, allowing free UEA/CEA payload execution - (File: x/uexecutor/keeper/fees.go)

### Summary
`Keeper.CalculateGasCost` unwraps the feemarket base fee's `LegacyDec` fixed-point encoding by dividing the raw internal big.Int by `1e18` *before* multiplying by `gasUsed`, instead of multiplying first and dividing (or truncating) last. This mirrors the Plaza Finance `getRedeemAmount()` root cause: division before multiplication causes the result to round to zero whenever the numerator (`baseFee`) is smaller than the divisor (`1e18` in Dec-fixed-point terms, i.e. a base fee below 1 whole `upc` per gas unit).

### Finding Description [1](#0-0) 

```go
baseFeeBig := baseFee.BigInt()
// @dev: ... base fee is always a whole number of upc -- no fractional upc exists.
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
...
effectiveGasPrice := new(big.Int).Set(baseFeeBig)
...
gasCost := new(big.Int).Mul(effectiveGasPrice, gasUsedBig)
```

The code assumes the feemarket's `baseFee` (a `sdkmath.LegacyDec`, which internally represents values as `value * 1e18`) is always a whole number of `upc`. This is a design assumption, not an enforced invariant: the EIP-1559-style feemarket module can adjust `baseFee` downward toward its configured minimum through ordinary block-utilization dynamics (blocks below the gas target lower `baseFee` each block), and nothing in this code path — nor in `DeductGasFeesFromReceipt` — validates that the current `baseFee` is ≥ `1` `upc`.

If `baseFee` ever settles below `1` upc (e.g. `0.5`), `baseFeeBig.Div(baseFeeBig, 1e18)` performs integer division and truncates to `0` *before* the multiplication by `gasUsed` in `CalculateGasCost`, at line 60 vs 81. The subsequent `gasCost = effectiveGasPrice * gasUsed` is `0 * gasUsed = 0` regardless of how much gas was actually consumed. `DeductGasFeesFromReceipt` then short-circuits on `gasCost.Sign() <= 0` and returns `nil` (no error, no fee charged): [2](#0-1) 

This is invoked from every UEA/CEA payload execution path (`ExecutePayloadV2`, `ExecuteInboundGasAndPayload`'s smart-contract branch) that bills the recipient's native `upc` balance for EVM gas consumed by `DerivedEVMCall`/`CallUEAExecutePayload`/`CallExecuteUniversalTx`: [3](#0-2) [4](#0-3) 

Compare to the correct order used elsewhere in the same file for gas cost estimation in the ante handler (`app/cosmos/min_gas_price.go`), where `Mul` happens before rounding — this confirms the multiply-then-divide/round pattern is the established safe convention in this codebase, and `CalculateGasCost` deviates from it.

### Impact Explanation
Under the Push Chain Allowed Impact Gate this maps to "corruption of ... gas fee accounting ... reachable from ordinary user deposits, payloads, contracts, or default transaction submission paths alone" and effectively an unauthorized/unbilled module-originated EVM execution: any unprivileged user who submits a `UniversalPayload`/CEA smart-contract call gets their real EVM gas consumption (`receipt.GasUsed`, potentially very large — payloads can specify high `gasLimit`) executed for free once the network's dynamic base fee drops below `1 upc`. This lets an attacker drain compute/state-transition resources from validators at zero cost repeatedly, and burns no `upc` even though real EVM work and possibly PRC20/fund movements occurred — a systemic underbilling of protocol-intended fee revenue triggered purely by ordinary market conditions (low chain utilization), not by any privileged actor.

### Likelihood Explanation
Feemarket base fee is not clamped away from sub-1-upc territory in this code path; it decreases automatically via standard EIP-1559 dynamics whenever blocks run below the gas target, which is a normal, attacker-influenceable condition (an attacker can simply avoid submitting gas-heavy txs for a period to let `baseFee` decay, then submit payload transactions once it crosses below 1 upc). Whether the on-chain minimum base fee parameter can actually reach sub-1 in this deployment's configured params could not be confirmed from the available index (feemarket params/genesis defaults are in an external `cosmos-evm` dependency not fully indexed here), so likelihood should be validated by checking the deployed `feemarket` `MinGasPrice`/`BaseFeeChangeDenominator` parameters before treating this as certain-to-trigger; the code-level rounding bug itself is confirmed and unconditional once the precondition (`baseFee < 1 upc`) holds.

### Recommendation
Change the order of operations in `CalculateGasCost` to multiply before dividing, mirroring the audit report's mitigation:
```go
gasCost := new(big.Int).Mul(baseFee.BigInt(), gasUsedBig) // baseFee.BigInt() is *1e18-scaled
gasCost.Div(gasCost, big.NewInt(1e18))
```
This preserves full precision until the final division, so a fractional base fee (e.g. 0.5 upc) times a large `gasUsed` still yields a correct non-zero, non-underbilled fee. Additionally, consider using `Ceil()`-style rounding (round up) rather than `Div` (rounds down) so the protocol never underbills gas, consistent with the `ceil(minGasPrice * gasLimit)` pattern already used in `app/cosmos/min_gas_price.go` and `app/ante/validator_tx_fee.go`.

### Proof of Concept
1. Let feemarket's `baseFee` decay (via low block gas utilization, or if configurable, set) to a value `< 1.0` `upc`, e.g. `0.9` (`LegacyDec` internal representation `9 * 10^17`).
2. An attacker submits any `UniversalPayload` (via `ExecutePayloadV2`) or an inbound `FUNDS_AND_PAYLOAD`/CEA smart-contract call (via `ExecuteInboundGasAndPayload`) whose EVM execution consumes substantial `gasUsed` (e.g. 10,000,000 gas).
3. In `CalculateGasCost`: `baseFeeBig = 9e17`; `baseFeeBig.Div(baseFeeBig, 1e18) = 0` (integer division truncates); `effectiveGasPrice = 0`; `gasCost = 0 * 10,000,000 = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without deducting/burning any `upc` from the recipient, even though the EVM call executed and consumed real gas.
5. Repeat arbitrarily many times while `baseFee` remains below `1 upc`, executing arbitrary payloads at zero cost to the caller.

### Citations

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

**File:** x/uexecutor/keeper/execute_payload.go (L39-48)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L238-256)
```go
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)

		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```
