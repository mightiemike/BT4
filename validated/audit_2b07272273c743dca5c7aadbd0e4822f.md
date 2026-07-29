## Finding [1](#0-0) 

### Title
Gas-fee truncation lets `MsgExecutePayload` execution become permanently free once the network base fee decays below 1 upc - (File: `x/uexecutor/keeper/fees.go`)

### Summary
`CalculateGasCost` truncates the EIP-1559 base fee to whole `upc` **before** multiplying by `gasUsed`, instead of truncating the final product. If the network base fee (a `LegacyDec` with 18-decimal internal precision) ever decays to a value representing less than 1 atomic `upc`, `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` rounds to exactly `0`, making the entire computed `gasCost` zero regardless of how much gas was actually consumed. `DeductGasFeesFromReceipt` then short-circuits on `gasCost.Sign() <= 0` and skips fee deduction entirely — this is the same rounding-to-zero pattern as the TokenLocker `penaltyOnAmount` bug, but here it zeroes out gas-fee accounting instead of a withdrawal penalty.

### Finding Description [2](#0-1) 

`CalculateGasCost` computes:
```
baseFeeBig := baseFee.BigInt()          // raw 1e18-scaled internal representation
baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))   // truncates to whole upc HERE, before scaling by gasUsed
...
gasCost := effectiveGasPrice * gasUsedBig
```
The code comments assert base fee is "always a whole number of upc," but this is an assumption about the upstream `feemarket` module's EIP-1559 base-fee-adjustment output — nothing in this repository enforces it. The `feemarket` base fee is a `LegacyDec` that adjusts every block by a percentage (elasticity/base-fee-change-denominator formula) and can asymptotically decay toward zero over consecutive low-usage blocks; there is no code here guaranteeing it never falls below `1e18` (i.e., below 1 atomic `upc`) in its raw representation. [3](#0-2) 

`DeductGasFeesFromReceipt` calls `CalculateGasCost` and, if the result is `<= 0`, returns `nil` — **skipping `DeductAndBurnFees` entirely**, with no fee charged no matter how large `receipt.GasUsed` was.

This path is reached from `MsgExecutePayload` (`x/uexecutor/keeper/msg_execute_payload.go`, `x/uexecutor/keeper/execute_payload.go`), which is itself in the ante-handler gasless allowlist: [4](#0-3) 

Because `MsgExecutePayload` already bypasses the Cosmos-level `DeductFeeDecorator` and `MinGasPriceDecorator` fee checks (it is gasless by design so UEA users need no gas token), the **only** place gas cost is ever charged for this flow is `DeductGasFeesFromReceipt`'s post-execution deduction from the UEA balance. Once base fee truncates to zero, that becomes a no-op too — meaning EVM execution of arbitrarily large `gasUsed` payloads becomes entirely free at every layer.

### Impact Explanation
Any unprivileged external caller can submit `MsgExecutePayload` transactions (a message any account may submit — see `x/uexecutor/README.md` authorization notes) at zero Cosmos-tx cost (it's gasless) and, once base fee decays below 1 upc, zero EVM gas-fee cost as well. This corrupts gas-fee accounting (nonzero `gasUsed` charged as zero fee) and provides a free, unbounded, and repeatable way to consume validator EVM execution resources (via `CallUEAExecutePayload` / `DerivedEVMCall`) without paying anything to the protocol — a resource-drain vector reachable purely from ordinary unprivileged transaction submission, matching "corruption of ... gas fee accounting" and unprivileged-reachable DoS in the allowed-impact gate.

### Likelihood Explanation
Base fee decay to sub-1-upc is a natural consequence of the standard EIP-1559 elasticity formula during sustained low network activity (a state the attacker can help induce simply by not competing for blockspace); genesis base fee values used in this repo's test/deploy configs (`~1e9` upc) require only on the order of ~150 successive below-target blocks (at up to ~12.5% decrease each, per typical elasticity settings) to fall under 1 upc — well within reach without any privileged control.

### Recommendation
Compute `gasCost` using the full-precision `LegacyDec` base fee multiplied by `gasUsed` first, and truncate only the final product to the atomic `upc` unit (i.e., `gasCost = baseFee.MulInt64(gasUsed).TruncateInt()` or equivalent), rather than truncating `baseFee` alone before scaling. Additionally, treat a computed `gasCost` of `0` with nonzero `gasUsed` as suspicious/loggable rather than silently skipping fee deduction, and consider enforcing a protocol-level minimum base fee floor.

### Proof of Concept
1. Let the chain run through a sustained period of low/no transaction activity so `feemarket`'s base fee decays (per its standard elasticity formula) until its `LegacyDec` value represents less than `1e18` in raw internal units (i.e., less than 1 `upc`).
2. Submit `MsgExecutePayload` (already gasless at the ante layer per `app/txpolicy/gasless.go`) targeting a UEA that triggers an expensive EVM call with large `receipt.GasUsed`.
3. In `CalculateGasCost`, `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` yields `0`, so `effectiveGasPrice = 0` and `gasCost = 0 * gasUsed = 0`.
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without calling `DeductAndBurnFees` — the UEA balance is never touched despite consuming real EVM gas.
5. Repeat in a loop: unlimited free EVM execution with zero cost charged anywhere in the pipeline.

### Citations

**File:** x/uexecutor/keeper/fees.go (L47-91)
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

	k.Logger().Debug("gas cost calculated",
		"base_fee", baseFee.String(),
		"effective_gas_price", effectiveGasPrice.String(),
		"gas_used", gasUsed,
		"gas_cost", gasCost.String(),
	)

	return gasCost, nil
}
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

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
