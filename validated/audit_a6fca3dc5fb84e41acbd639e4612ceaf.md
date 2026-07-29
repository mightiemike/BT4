### Title
Missing zero-amount guard before `CallPRC20Deposit` in `handleFailedOutbound` can strand a failed outbound in `ABORTED` state - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
The external report's bug class is: a downstream token contract reverts on a zero-amount transfer, and the calling contract does not skip the transfer when the computed amount is zero, causing an otherwise-valid finalize/refund flow to revert entirely. The same *pattern* (unconditional external call with an amount that can legitimately be zero) exists in Push Chain's outbound-finalization path: `handleFailedOutbound` unconditionally calls `CallPRC20Deposit` to re-mint bridged funds for revert recipients whenever `outbound.TxType` is `FUNDS`, `GAS_AND_PAYLOAD`, or `FUNDS_AND_PAYLOAD`, without checking whether `outbound.Amount` is `"0"` first.

### Finding Description
`handleFailedOutbound` re-mints bridged tokens back to the revert recipient for `FUNDS`/`GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD` outbounds: [1](#0-0) 

Note that `amount` is parsed directly from `outbound.Amount` and passed straight into `CallPRC20Deposit` — there is no `if amount.Sign() > 0` guard here, unlike the sibling function `applyGasRefund`, which explicitly checks and skips the call when there's nothing to refund: [2](#0-1) 

`CallPRC20Deposit` forwards the amount as-is into a `DerivedEVMCall` to the `UniversalCore`/PRC20 `depositPRC20Token` system contract: [3](#0-2) 

The codebase's own test suite confirms that `FUNDS_AND_PAYLOAD` (and `GAS_AND_PAYLOAD`) inbounds/outbounds with `Amount == "0"` are an explicitly supported, non-error case (e.g. payload-only execution with zero value moved): [4](#0-3) 

If such a zero-amount `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` outbound is later voted as failed by Universal Validators (`MsgVoteOutbound` with `success=false`), `handleFailedOutbound` will call `CallPRC20Deposit` with `amount = 0`. If the underlying `depositPRC20Token`/PRC20 system contract enforces the same "no zero transfer" invariant that `WDIA` does in the external report (which is plausible given `WDIA` is part of the same system-contract family used for gas/PRC20 accounting on Push Chain), this call will revert.

When `CallPRC20Deposit` returns an error, `handleFailedOutbound` does not treat it as a benign no-op — it calls `AbortOutbound`, which marks the outbound `ABORTED` and requires manual/admin intervention rather than completing the normal `REVERTED` finalization: [5](#0-4) 

### Impact Explanation
If the PRC20/`UniversalCore` `depositPRC20Token` path rejects zero-amount deposits (mirroring `WDIA`'s `ZeroTransferAmount` check), any zero-amount `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` outbound that a Universal Validator honestly reports as failed on the destination chain will get stuck in `Status_ABORTED` instead of completing the normal revert/refund flow. This is a state-freezing condition on the `UniversalTx`/`OutboundTx` record reachable purely from an ordinary user's zero-value cross-chain payload call plus honest UV voting — no privileged action or malicious validator required — matching the in-scope "permanent freezing ... of user or protocol-controlled funds/state" and "corruption of ... canonical UniversalTx state" categories. It does not directly cause fund loss (since amount is zero, nothing is owed), but it does corrupt the expected outbound lifecycle and requires manual admin recovery, which is itself an availability/invariant violation for that UTX.

### Likelihood Explanation
Reachability requires: (1) a `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` inbound/outbound with amount `"0"` — already an explicitly tested, valid path in this codebase — and (2) an honest UV vote that the corresponding outbound execution failed on the destination chain. Both conditions are ordinary, unprivileged occurrences (e.g., a pure-payload cross-chain call whose destination-chain execution genuinely fails). The likelihood is moderate: it depends on whether the actual PRC20/`UniversalCore` Solidity contract (not present in this Go repository, so its exact revert semantics could not be directly confirmed here) enforces a `WDIA`-style zero-transfer rejection on `depositPRC20Token`. Given the report explicitly ties this bug class to `WDIA`, which is part of the same system-contract family, this is a credible but unverified assumption from within this repo's scope alone.

### Recommendation
Add an explicit `amount.Sign() > 0` guard around the `CallPRC20Deposit` call in `handleFailedOutbound`, mirroring the pattern already used in `applyGasRefund`, so that zero-amount reverts never attempt a re-mint call and instead proceed directly to `Status_REVERTED`/gas-refund handling. Additionally, confirm with the `UniversalCore`/PRC20 Solidity contracts (outside this repo) whether `depositPRC20Token` rejects zero-amount calls, and if so, either relax that check or guard every module-side caller (`CallPRC20Deposit`, `CallPRC20DepositAutoSwap`, `CallUniversalCoreRefundUnusedGas`) consistently.

### Proof of Concept
1. Submit a `FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`) inbound with `Amount = "0"` and a payload — validated and executed as shown in `TestInboundZeroAmountFundsAndPayload` [6](#0-5) .
2. Suppose this flow produces a downstream `FUNDS_AND_PAYLOAD` outbound with `Amount = "0"` (e.g., a payload that triggers an outbound call with no value moved).
3. Have the required threshold of Universal Validators honestly vote `MsgVoteOutbound` with `success = false` (destination-chain execution genuinely failed).
4. `VoteOutbound` invokes `FinalizeOutbound` → `handleFailedOutbound`, which parses `amount = 0` and calls `k.CallPRC20Deposit(ctx, prc20, recipient, 0)` unconditionally [7](#0-6) .
5. If the target `depositPRC20Token` system contract reverts on a zero-amount transfer (per the `WDIA` precedent), `CallPRC20Deposit` returns an error, and the outbound is marked `Status_ABORTED` via `AbortOutbound` instead of `Status_REVERTED` [5](#0-4) , leaving the UTX/outbound state stuck pending manual admin recovery.

**Uncertainty note:** I could not locate the `depositPRC20Token`/PRC20 Solidity source in this repository (it is referenced only via ABI/system-contract address, e.g. `x/uexecutor/types/abi.go`), so I could not directly confirm whether it enforces a zero-transfer revert. This finding is reported as a structural analog (missing zero-amount guard, inconsistent with the guard already present in `applyGasRefund`) with plausible but unverified downstream revert behavior.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L102-119)
```go
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
```

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/keeper/outbound.go (L193-196)
```go
	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}
```

**File:** x/uexecutor/keeper/evm.go (L262-303)
```go
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```

**File:** test/integration/uexecutor/inbound_zero_amount_test.go (L115-173)
```go
func TestInboundZeroAmountFundsAndPayload(t *testing.T) {
	t.Run("zero amount FUNDS_AND_PAYLOAD skips deposit and executes payload", func(t *testing.T) {
		chainApp, ctx, vals, coreVals, ueaAddrHex := setupZeroAmountInboundTest(t, 4)
		usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr
		testAddress := utils.GetDefaultAddresses().DefaultTestAddr

		validUP := &uexecutortypes.UniversalPayload{
			To:                   ueaAddrHex.String(),
			Value:                "0",
			Data:                 "0xa9059cbb000000000000000000000000527f3692f5c53cfa83f7689885995606f93b616400000000000000000000000000000000000000000000000000000000000f4240",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		}

		inbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xzeroamt01",
			Sender:           testAddress,
			Recipient:        "",
			Amount:           "0",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
		}

		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}

		isPending, err := chainApp.UexecutorKeeper.IsPendingInbound(ctx, *inbound)
		require.NoError(t, err)
		require.False(t, isPending, "inbound should be executed after quorum")

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "universal tx should exist after quorum")

		// No deposit PCTx should be recorded (deposit was skipped)
		// Only payload-related PCTxs should exist
		require.NotEmpty(t, utx.PcTx, "PcTx should not be empty — payload execution should be recorded")

		// No INBOUND_REVERT should be created (deposit was skipped, not failed)
		for _, ob := range utx.OutboundTx {
			require.NotEqual(t, uexecutortypes.TxType_INBOUND_REVERT, ob.TxType,
				"no INBOUND_REVERT should be created for zero-amount FUNDS_AND_PAYLOAD")
		}
	})
```
