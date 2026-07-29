## Analysis

The Aave report describes a fee-computation function (`calculateLoanOriginationFee`) that silently returns **zero** for legitimate non-zero amounts because of truncating fixed-point math, letting the caller skip paying the fee entirely, with no revert-on-zero guard.

The closest reachable analog in this repository is the gas-fee accounting used for UEA/CEA payload execution in `x/uexecutor`, not the smart-contract-side loan fee. I want to flag this clearly as a **lower-confidence candidate** — I was unable to inspect the vendored `cosmossdk.io/math.LegacyDec.BigInt()` implementation or the live feemarket base-fee decay parameters/floor used in mainnet/testnet configs from within this repo, so the exact reachability boundary (how low the dynamic base fee can actually go) is not fully confirmed.

### Title
Truncating base-fee unwrap can zero out UEA/CEA gas-fee billing - (File: x/uexecutor/keeper/fees.go)

### Summary
`CalculateGasCost` unwraps the feemarket's dynamic `baseFee` (an 18-decimal `LegacyDec`) back into a plain integer via `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` [1](#0-0) , based on an inline assumption that "the base fee is always a whole number of upc -- no fractional upc exists." [2](#0-1)  If the actual dynamic base fee (which is adjusted every block by the standard EIP-1559-style feemarket algorithm) ever decays below one whole `upc` unit, this integer division truncates it to `0`, and `effectiveGasPrice`/`gasCost` become `0` regardless of `gasUsed` [3](#0-2) .

### Finding Description
`DeductGasFeesFromReceipt` calls `CalculateGasCost` and then explicitly no-ops when the resulting cost is non-positive: `if gasCost.Sign() <= 0 { return nil }` [4](#0-3) . This function is the sole gas-billing mechanism for payloads executed through a Universal Executor Account, invoked from every payload-execution path: `ExecutePayloadV2` [5](#0-4) , the direct `ExecutePayload` message handler [6](#0-5) , and inbound CEA/smart-contract execution flows [7](#0-6) [8](#0-7) .

An unprivileged user submitting `MsgExecutePayload` (which is user-reachable and does not require special permissions — it's whitelisted for gasless treatment in the ante pipeline as well [9](#0-8) ) triggers this billing path on every execution. If the network's dynamic EIP-1559 base fee ever settles to a value whose whole-`upc` component is `0` (i.e., below `1e18` in the `LegacyDec`'s internal representation) — a state reachable purely by sustained low block utilization, no privileged actor required — `CalculateGasCost` returns `0`, and `DeductGasFeesFromReceipt` silently skips billing while the EVM call itself still executes and consumes real compute/storage resources at protocol expense (gas is still burned by the module account under `DeductAndBurnFees`, which only fires when `gasCost` is positive).

### Impact Explanation
If reachable, this allows unprivileged users to execute UEA-routed EVM payloads (arbitrary calldata, arbitrary gas usage up to the payload's `GasLimit`) without paying the corresponding `upc` gas fee, which is a direct "fee-less execution" resource-drain analogous to the cited "fee-less loans" bug class — protocol/user funds normally burned as gas fee are foregone, and there is no economic disincentive against spamming gas-heavy payloads during any period the base fee sits below one whole `upc`.

### Likelihood Explanation
Uncertain / not fully confirmed. This depends on (a) the exact vendored behavior of `cosmossdk.io/math.LegacyDec.BigInt()` (whether it returns the raw scaled internal integer or something else), and (b) whether the deployed feemarket parameters (elasticity multiplier, base-fee-change denominator, and any configured minimum) ever allow the base fee to decay below `1e18` in practice. I could not verify the deployed `MinGasPrice`/floor values for mainnet or testnet-donut configs from the indexed code, nor confirm that the feemarket's per-block base-fee adjustment can produce a sub-whole-unit value under real traffic patterns. This should be verified with a live/replicated environment before being treated as confirmed.

### Recommendation
- Do not rely on the "base fee is always a whole upc" assumption; instead, preserve full `LegacyDec` precision (or floor with `TruncateInt`/`Ceil` deliberately and document the rounding direction) when computing `effectiveGasPrice` in `CalculateGasCost`.
- Add an explicit guard: if the computed base fee (as a whole number of `upc`) rounds to `0` while the underlying decimal `baseFee` is actually positive, either revert (charge a minimum fee) rather than silently returning a zero cost, mirroring the Aave fix's "reject the zero-fee case" approach.
- Add unit tests for `CalculateGasCost` with sub-`1e18`, fractional `LegacyDec` base-fee inputs (not just whole-number synthetic bases as currently used in test setup, e.g. `sdkmath.NewInt(1000000000000000000)` in `test/utils/setup_app.go` [10](#0-9) ) to lock in the intended truncation/rounding behavior.

### Proof of Concept
Not independently reproducible from static analysis alone given the above uncertainties. A concrete PoC would require: (1) confirming `LegacyDec.BigInt()`'s exact semantics in the vendored cosmos-sdk version, and (2) driving the feemarket module (via low-utilization blocks) or directly via `FeeMarketKeeper.SetBaseFee` in a test harness to a value whose `LegacyDec` numeric value is less than `1` (e.g. `0.5`), then calling `ExecutePayload`/`ExecutePayloadV2` and asserting `DeductGasFeesFromReceipt` returns `nil` and no bank debit occurs despite non-zero `receipt.GasUsed`.

### Citations

**File:** x/uexecutor/keeper/fees.go (L53-60)
```go
	baseFeeBig := baseFee.BigInt()
	// @dev: LegacyDec stores values with 18-decimal precision internally, so 1 upc = 1e18
	// in the LegacyDec representation. Since 1 upc is the smallest denomination (like wei
	// in Ethereum), the base fee is always a whole number of upc -- no fractional upc exists.
	// This division unwraps the LegacyDec encoding back to the actual upc amount.
	// Note: baseFee.BigInt() returns a reference to the internal big.Int; the in-place Div
	// mutates it, which is safe here since baseFee is a local value-type copy.
	baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))
```

**File:** x/uexecutor/keeper/fees.go (L74-81)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L250-256)
```go
		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L250-256)
```go
				if contractErr == nil {
					feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
					if feeErr == nil {
						writeCache()
					}
				}
			}
```

**File:** app/txpolicy/gasless.go (L17-26)
```go
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

**File:** test/utils/setup_app.go (L151-152)
```go
	baseFee := sdkmath.NewInt(1000000000000000000)                  // Int
	app.FeeMarketKeeper.SetBaseFee(ctx, sdkmath.LegacyDec(baseFee)) // Dec
```
