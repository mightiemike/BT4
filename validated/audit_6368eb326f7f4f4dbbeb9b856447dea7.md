I have enough evidence to write up this finding. This is a genuine analog: `x/uexecutor/keeper/fees.go`'s `CalculateGasCost` uses truncating integer division on the `LegacyDec` base fee, and callers treat a zero result as "nothing to charge" — an unprivileged user can make gas fees round to zero for smart-contract-recipient CEA/UEA executions.

### Title
Gas fee for UEA/CEA universal execution can round down to zero, letting recipients skip gas cost deduction - (File: x/uexecutor/keeper/fees.go)

### Summary
`Keeper.CalculateGasCost` in `x/uexecutor/keeper/fees.go` converts the feemarket's `LegacyDec` base fee to a whole `upc` amount using Solidity/Go-style truncating integer division (`baseFeeBig.Div(baseFeeBig, big.NewInt(1e18))`). Whenever the current base fee is below `1 upc` (i.e., a `LegacyDec` value < `1.0`), this division truncates the effective gas price to `0`, making the entire computed `gasCost` `0` regardless of `gasUsed`. `DeductGasFeesFromReceipt` explicitly treats a non-positive `gasCost` as "nothing to deduct" and returns `nil` without transferring or burning any fee [1](#0-0) , mirroring the reported Primitive Protocol pattern where integer-division-to-zero silently waives an expected fee.

### Finding Description
`CalculateGasCost` computes the effective gas price by unwrapping the `LegacyDec`-encoded `baseFee` back into whole `upc` via integer division by `1e18`: [2](#0-1) 

If the feemarket's dynamically-adjusted base fee (per EIP-1559-style mechanics in `x/feemarket`) is at any point less than `1.0` in its `LegacyDec` representation — i.e., less than 1 whole `upc` per unit of gas — `baseFeeBig.Div(...)` truncates to `0`. The function then computes `effectiveGasPrice = 0`, so `gasCost = effectiveGasPrice * gasUsed = 0`, for any `gasUsed` value, however large.

This feeds directly into `DeductGasFeesFromReceipt`, called from the universal execution path when a `DerivedEVMCall`/CEA execution completes and gas must be charged back to the recipient smart account: [3](#0-2) 

The `if gasCost.Sign() <= 0 { return nil }` guard means: whenever the base fee truncates to zero, the function returns success with **no fee deducted and no burn performed**, even though real EVM execution gas (`receipt.GasUsed`) was consumed. `DeductAndBurnFees` — the only accounting entry point for this charge — is simply never invoked.

Unlike the original Primitive Protocol report (attacker chooses a tiny exercise amount to zero the fee), here the "small value" is the network's own base fee, which is a chain-state variable, not directly attacker-controlled — but it is fully deterministic and externally observable, and any unprivileged user submitting a normal inbound/UEA/CEA payload during a low-base-fee period benefits automatically and silently, without needing any privileged access.

### Impact Explanation
This corrupts gas fee accounting in the universal execution path (`x/uexecutor`): recipients of `executeUniversalTx` calls avoid paying for EVM gas actually consumed on Push Chain whenever the feemarket base fee is below 1 `upc`. This falls under "corruption of ... gas fee accounting" in the allowed impact list. It does not directly drain user funds, but it is a fee-bypass/free-execution vulnerability against the protocol's own gas cost recovery mechanism, and is deterministic and reproducible by any unprivileged caller once the network base fee condition is met.

### Likelihood Explanation
Genesis and test configurations in this repo set `base_fee` far above `1e18` (e.g. `1000000000` upc), which currently prevents this from triggering under default settings [4](#0-3) . However, `x/feemarket`'s base fee is designed to move dynamically with block gas utilization, and there is no explicit floor check in `CalculateGasCost` or `DeductGasFeesFromReceipt` preventing the base fee from decaying toward or below `1 upc` over time on low-traffic chains/environments, or on any deployment/testnet where `min_gas_price`/`base_fee` params are configured lower. Because the bug is a silent no-op (no error, no revert) rather than a loud failure, it would likely go unnoticed until fee revenue analysis reveals free executions.

### Recommendation
In `CalculateGasCost` (`x/uexecutor/keeper/fees.go`), avoid truncating-to-zero division: either round up (ceiling) when converting the `LegacyDec` base fee to whole `upc`, keep the calculation in `LegacyDec`/rational form until the final gas cost is computed (only truncating once, at the very end, and rounding up), or explicitly reject/charge a minimum non-zero fee when `gasUsed > 0` but the computed cost rounds to zero. At minimum, treat `gasCost.Sign() <= 0 && gasUsed > 0` as an error/alert condition rather than a silent "nothing to deduct" success path.

### Proof of Concept
1. Set (or let the feemarket organically settle to) `FeeMarketKeeper.GetBaseFee(ctx)` returning a `LegacyDec` value less than `1.0` (i.e., representing less than 1 `upc` per gas unit), e.g. `sdkmath.LegacyNewDecWithPrec(5, 1)` (0.5).
2. Submit/observe an inbound CEA payload that results in a smart contract recipient executing via `executeUniversalTx`, consuming non-zero `GasUsed` (e.g., as in `test/integration/uexecutor/inbound_cea_smart_contract_test.go`, `TestInboundCEASmartContractRecipient`) [5](#0-4) .
3. Observe `DeductGasFeesFromReceipt` compute `gasCost = 0` via `CalculateGasCost`, hit the `gasCost.Sign() <= 0` branch, and return `nil` without calling `DeductAndBurnFees`.
4. Verify the recipient's `upc` balance is unchanged after execution despite non-zero `receipt.GasUsed`, unlike the expected-decrease behavior asserted in the existing test at lines 347–351 of the same test file.

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

**File:** testnet/core/setup/setup_genesis_validator.sh (L152-154)
```shellscript
  update_test_genesis '.app_state["feemarket"]["params"]["no_base_fee"]=false'
  update_test_genesis '.app_state["feemarket"]["params"]["base_fee"]="1000000000.000000000000000000"'
  update_test_genesis '.app_state["feemarket"]["params"]["min_gas_price"]="1000000000.000000000000000000"'
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
