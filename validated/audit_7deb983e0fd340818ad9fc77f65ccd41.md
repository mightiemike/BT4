I have sufficient evidence to identify a valid analog. Note that `min_gas_price.go` uses `.Ceil().RoundInt()` (rounding up, in the protocol's favor) for the ante-level fee check, but `CalculateGasCost` in `x/uexecutor/keeper/fees.go` uses plain integer `Div` (rounds down, floor) to convert the `LegacyDec` base fee back into whole `upc`, then explicitly treats a non-positive result as "nothing to charge." This is the same rounding-direction bug class as the StUSR report: truncation favors the payer, and when the truncated value hits zero, a real, metered EVM execution is let through with **zero fee actually charged** — the DerivedEVMCall itself still consumed real gas paid by the protocol/module account.

### Title
Gas-fee truncation in `CalculateGasCost` lets metered EVM execution proceed with zero fee charged - (File: x/uexecutor/keeper/fees.go)

### Summary
`DeductGasFeesFromReceipt` charges a smart-account recipient for gas consumed during `executeUniversalTx`/payload execution by calling `CalculateGasCost`, which floors the `LegacyDec` base fee to a whole `upc` integer via `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` [1](#0-0) . If the fee-market's live base fee (a `LegacyDec`, which is not contractually guaranteed to always be a whole `upc` despite the comment's assumption) is below `1e18` (i.e., less than 1 whole `upc`), this integer division truncates it to `0`, making `effectiveGasPrice = 0` and therefore `gasCost = effectiveGasPrice * gasUsed = 0` regardless of how much real gas was consumed [2](#0-1) .

### Finding Description
`DeductGasFeesFromReceipt` explicitly short-circuits when the computed cost is non-positive: `if gasCost.Sign() <= 0 { return nil }` [3](#0-2) . This mirrors the StUSR pattern exactly: a rounding-to-zero condition on the "amount owed" side, occurring silently in a hot path, that is treated as a legitimate no-op rather than an underflow error. This routine is invoked after real, receipt-bearing EVM calls executed via `DerivedEVMCall` on behalf of a user's UEA/smart-contract recipient (e.g., in the CEA smart-contract flow demonstrated by `test/integration/uexecutor/inbound_cea_smart_contract_test.go`, where the recipient's `upc` balance is expected to decrease after `executeUniversalTx`) [4](#0-3) . The module account itself still incurs the real gas cost of the `DerivedEVMCall` on Push Chain's own EVM, so a base fee that floors to zero converts real protocol-borne execution cost into an un-recouped loss, without any error being raised.

### Impact Explanation
This corrupts gas fee accounting/refund accounting, one of the explicitly named in-scope invariants ("gas fee accounting, refund accounting"). An attacker submitting inbound payloads that route through `ExecuteInboundFundsAndPayload`/`executeUniversalTx` during any block window where the live `LegacyDec` base fee sits below `1` whole `upc` gets metered EVM execution for free, at the module's expense, repeatedly and at scale — a systemic, unprivileged drain of protocol-held funds via gas-cost shifting, not merely a display bug. Unlike the DecCoins-based ante-level `MinGasPriceDecorator`, which deliberately rounds up (`Ceil().RoundInt()`) to protect the protocol [5](#0-4) , this post-execution accounting path rounds down and silently zeroes out, inverting the safe rounding direction used elsewhere in the same codebase.

### Likelihood Explanation
Likelihood depends on whether the deployed `feemarket` base fee can realistically fall under `1e18` (1 whole `upc`) in `LegacyDec` terms. All observed genesis/test configurations set `base_fee` to values like `"1000000000.000000000000000000"` (i.e., far above 1) [6](#0-5) , so under current default deployments the truncation-to-zero edge case is not immediately hit. However, the fee-market module implements EIP-1559-style dynamic adjustment, and the code's own comment acknowledges this is an *assumption* ("the base fee is always a whole number of upc") rather than an enforced invariant — nothing in `CalculateGasCost` or `DeductGasFeesFromReceipt` validates or rejects a sub-1-upc base fee before truncating it. If the base fee is ever allowed to drop below `1e18` in `LegacyDec` (via governance param changes, congestion-driven decreases, or future base-fee-formula changes), the zero-cost condition triggers deterministically and repeatably for every subsequent payload execution.

### Recommendation
Do not silently truncate the `LegacyDec` base fee to an integer via floor division. Either (a) perform the gas-cost multiplication in `LegacyDec` space and round up (`Ceil()`) only at the very end when converting to the `upc` integer amount to charge, matching the protective rounding direction already used in `app/cosmos/min_gas_price.go`, or (b) explicitly reject/floor-guard so that a sub-1-`upc` base fee never resolves to a `0` `effectiveGasPrice`, and treat `gasCost.Sign() <= 0` as an error condition (or a minimum non-zero charge) rather than a valid "nothing to deduct" case, since `receipt.GasUsed > 0` at that point.

### Proof of Concept
1. Set (or allow via governance/dynamic adjustment) `feemarket` base fee such that `k.feemarketKeeper.GetBaseFee(sdkCtx)` returns a `LegacyDec` value below `1e18` (e.g., `0.5e18`, representing 0.5 `upc`).
2. Submit an inbound `FUNDS_AND_PAYLOAD`/CEA payload that resolves to a smart-contract recipient and triggers `executeUniversalTx` via `DerivedEVMCall`, consuming nonzero real gas (`receipt.GasUsed > 0`).
3. In `CalculateGasCost`, `baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))` truncates `0.5e18` to `0`; `effectiveGasPrice = 0`, so `gasCost = 0 * gasUsed = 0` [7](#0-6) .
4. `DeductGasFeesFromReceipt` sees `gasCost.Sign() <= 0` and returns `nil` without calling `DeductAndBurnFees`, so the recipient's `upc` balance is untouched despite real gas having been spent by the module account executing the call [3](#0-2) .
5. Repeat across many inbound payloads during the low-base-fee window to accumulate unrecouped protocol gas expenditure.

### Citations

**File:** x/uexecutor/keeper/fees.go (L53-88)
```go
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

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L315-352)
```go
	t.Run("gas fees deducted from smart contract recipient after executeUniversalTx", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals, contractAddr := setupInboundCEASmartContractTest(t, 4)

		// Fund the smart contract with upc so fee deduction can succeed
		contractAccAddr := sdk.AccAddress(contractAddr.Bytes())
		fundCoins := sdk.NewCoins(sdk.NewInt64Coin("upc", 1_000_000_000))
		require.NoError(t, chainApp.BankKeeper.MintCoins(ctx, "mint", fundCoins))
		require.NoError(t, chainApp.BankKeeper.SendCoinsFromModuleToAccount(ctx, "mint", contractAccAddr, fundCoins))

		balanceBefore := chainApp.BankKeeper.GetBalance(ctx, contractAccAddr, "upc")

		// Reach quorum
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}

		// Verify executeUniversalTx PCTx has gas_used > 0
		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.GreaterOrEqual(t, len(utx.PcTx), 2, "should have deposit + executeUniversalTx PCTxs")

		callPcTx := utx.PcTx[1]
		require.Equal(t, "SUCCESS", callPcTx.Status)
		require.Greater(t, callPcTx.GasUsed, uint64(0), "executeUniversalTx should report gas used")

		// Verify upc balance decreased (gas was deducted)
		balanceAfter := chainApp.BankKeeper.GetBalance(ctx, contractAccAddr, "upc")
		require.True(t, balanceAfter.Amount.LT(balanceBefore.Amount),
			"smart contract upc balance should decrease after gas fee deduction (before=%s, after=%s)",
			balanceBefore.Amount, balanceAfter.Amount)
	})
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

**File:** scripts/test_node.sh (L118-120)
```shellscript
  update_test_genesis '.app_state["feemarket"]["params"]["no_base_fee"]=false'
  update_test_genesis '.app_state["feemarket"]["params"]["base_fee"]="1000000000.000000000000000000"'
  update_test_genesis '.app_state["feemarket"]["params"]["min_gas_price"]="1000000000.000000000000000000"'
```
