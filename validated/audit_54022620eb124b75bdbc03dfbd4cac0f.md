## Finding: Attacker-Controlled `Recipient` in isCEA Inbound Flow Enables Unauthorized Gas-Fee Drain From Arbitrary Funded Accounts

### Summary
In the `isCEA` inbound execution path, `ueaAddr` (the account that will be billed for EVM gas) is taken directly from `utx.InboundTx.Recipient`, a field fully controlled by whoever submits the source-chain deposit. There is no check binding `Recipient` to `Sender`, to the payload's signer, or to `VerificationData`. Because `DeductGasFeesFromReceipt` runs — and burns real `upc` — whenever the resulting EVM call returns any non-zero `GasUsed`, *regardless of whether the call succeeded or the signature verification inside the target contract failed*, an attacker can name any funded UEA/contract/EOA as `Recipient`, submit a garbage or self-authorized payload, and cause that unrelated account to be repeatedly and unwillingly debited.

### Finding Description
`utx.InboundTx.Recipient` is parsed directly into `ueaAddr` with no ownership check: [1](#0-0) 

This `ueaAddr` is later passed as the `recipient`/gas-payer to `ExecutePayloadV2` (UEA path) and directly to `DeductGasFeesFromReceipt` (smart-contract path): [2](#0-1) [3](#0-2) 

`ExecutePayloadV2` explicitly deducts fees "regardless of success/failure" of the payload call — a failed/reverted `CallUEAExecutePayload` (e.g. invalid signature, garbage `VerificationData`) still bills `ueaAddr` as long as `receipt.GasUsed > 0`: [4](#0-3) 

`DeductGasFeesFromReceipt` only skips billing when `receipt == nil || receipt.GasUsed == 0`; otherwise it calls `DeductAndBurnFees`, which does `SendCoinsFromAccountToModule` + `BurnCoins` against the `recipient` account with **no check that `recipient` authorized or is even related to this transaction**: [5](#0-4) [6](#0-5) 

`Recipient` is populated straight from source-chain event data captured by the universal client with no correlation to `Sender` or to a valid `VerificationData` signature — it is attacker-controlled the moment they craft the source-chain deposit/calldata: [7](#0-6) 

Existing project tests explicitly confirm this design property — "isCEA=true uses recipient directly ... without factory lookup by sender" and "recipient is not the sender's UEA" — i.e. the code intentionally decouples `Sender` (who pays/triggers) from `Recipient` (whose UEA/account state and balance are touched): [8](#0-7) [9](#0-8) 

Signature authentication of the payload happens inside the external UEA Solidity contract (out of this repo's scope), verifying `VerificationData` against the target UEA's stored owner key — not against `Sender`. Since the attacker does not control the victim's private key, a forged `VerificationData` will cause `executeUniversalTx` to revert, preventing state-mutating unauthorized execution. **However, the revert does not prevent fee deduction**: any non-zero `GasUsed` on that reverted (or even no-code-EOA) call still triggers `DeductAndBurnFees` against `Recipient`.

### Impact Explanation
An unprivileged attacker who fully controls a source-chain deposit transaction (real ordinary user deposit flow, honestly relayed by Universal Validators) can:
1. Set `Recipient` to any address holding `upc` balance (a victim's UEA, a plain EOA, or a deployed contract — no relationship to the attacker's own `Sender`/UEA required).
2. Set `TxType` to `GAS_AND_PAYLOAD` or `FUNDS_AND_PAYLOAD` with `IsCEA = true` and an arbitrary/garbage `UniversalPayload` + `VerificationData`.
3. Cause `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` to invoke `ExecutePayloadV2`/`CallExecuteUniversalTx` against the victim's account, and — win or lose on the EVM call — `DeductGasFeesFromReceipt` burns real `upc` from the **victim's** balance, not the attacker's.
4. Repeat this arbitrarily many times (each is a separate cheap deposit on the source chain) to drain the victim's `upc` balance to zero over time, without the victim ever signing or authorizing anything.

This is an unauthorized, protocol-level burn of a third party's funds — squarely in the "unauthorized burn ... of user ... funds" / "corruption of ... gas fee accounting" impact categories, reachable entirely through ordinary user deposit flows with honest validators and honest nodes.

### Likelihood Explanation
High. No privileged role is required — only the ability to submit a deposit event on any supported source chain naming an arbitrary `Recipient`, which any external-chain user can do. The relevant code paths (`isCEA` branch) are explicitly designed to let `Sender` differ from `Recipient` with no relationship check, and the fee-deduction call sites explicitly bill regardless of payload success (`ExecutePayloadV2`, `CallExecuteUniversalTx` in `execute_inbound_gas_and_payload.go`/`execute_inbound_funds_and_payload.go`). Per-call loss is bounded by real EVM gas cost, but is repeatable at attacker's discretion, making cumulative drain material.

### Recommendation
- Before charging gas fees via `DeductGasFeesFromReceipt` in the `isCEA` path, require that the EVM call actually succeeded (i.e., skip billing when `execErr != nil` from `CallUEAExecutePayload`/`CallExecuteUniversalTx`), or
- Bill gas costs to the `Sender`-derived account (the party that actually authorized/paid for the cross-chain action) rather than to the attacker-chosen `Recipient`, or
- Require verified authorization (a valid signature check performed by the chain module itself, not only the external contract) before any balance mutation is attempted against `Recipient`.

### Proof of Concept
1. Attacker holds a normal external-chain account with no relation to a victim's Push Chain UEA (`victimUEA`), which has `upc` balance.
2. Attacker triggers a deposit on the source chain gateway naming `recipient = victimUEA`, `sender = attacker`, `IsCEA = true`, `TxType = GAS_AND_PAYLOAD`, an arbitrary `UniversalPayload`, and garbage `VerificationData`.
3. Universal Validators honestly observe and vote this event; `ExecuteInboundGasAndPayload` runs, calls `CallFactoryGetOriginForUEA` (true — `victimUEA` is a real UEA), deposits/auto-swaps funds into `victimUEA`, then calls `ExecutePayloadV2(ctx, ueModuleAddr, victimUEA, payload, garbageVerificationData)`.
4. `CallUEAExecutePayload` reverts inside the UEA contract (invalid signature) but returns a receipt with `GasUsed > 0`.
5. `DeductGasFeesFromReceipt(cacheCtx, cacheCtx, victimUEA, receipt, payload)` executes `DeductAndBurnFees`, calling `SendCoinsFromAccountToModule(ctx, victimUEA, ...)` — debiting `victimUEA`'s `upc` balance for gas the victim never authorized.
6. Assert: `victimUEA`'s `upc` balance decreases despite the victim never signing/submitting anything, and despite `execErr != nil` (failed, unauthorized payload attempt).

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L67-70)
```go
				if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
					execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
				} else {
					ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L290-298)
```go
	// --- Step 6: execute payload
	k.Logger().Debug("executing payload via UEA (gas+payload)", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	receipt, err = k.ExecutePayloadV2(
		ctx,
		ueModuleAddr,
		ueaAddr,
		utx.InboundTx.UniversalPayload,
		utx.InboundTx.VerificationData,
	)
```

**File:** x/uexecutor/keeper/execute_payload.go (L39-53)
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

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```

**File:** x/uexecutor/keeper/fees.go (L21-37)
```go
func (k Keeper) DeductAndBurnFees(ctx context.Context, from sdk.AccAddress, gasCost *big.Int) error {
	amt := sdkmath.NewIntFromBigInt(gasCost)
	coin := sdk.NewCoin(pchaintypes.BaseDenom, amt)

	k.Logger().Debug("deducting and burning fees",
		"from", from.String(),
		"gas_cost", gasCost.String(),
		"denom", pchaintypes.BaseDenom,
	)

	err := k.bankKeeper.SendCoinsFromAccountToModule(ctx, from, types.ModuleName, sdk.NewCoins(coin))
	if err != nil {
		return err
	}

	return k.bankKeeper.BurnCoins(ctx, types.ModuleName, sdk.NewCoins(coin))
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

**File:** universalClient/chains/common/event_processor.go (L287-312)
```go
	inboundMsg := &uexecutortypes.Inbound{
		SourceChain: eventData.SourceChain,
		TxHash:      txHashHex,
		Sender:      eventData.Sender,
		Recipient:   eventData.Recipient,
		Amount:      eventData.Amount,
		AssetAddr:   eventData.Token,
		LogIndex:    strconv.FormatUint(uint64(eventData.LogIndex), 10),
		TxType:      txType,
		IsCEA:       eventData.FromCEA,
		RawPayload:  eventData.RawPayload,
	}

	// Set revert instructions if revert fund recipient is present
	if eventData.RevertFundRecipient != "" {
		inboundMsg.RevertInstructions = &uexecutortypes.RevertInstructions{
			FundRecipient: eventData.RevertFundRecipient,
		}
	}

	// Use event's VerificationData if present, otherwise fall back to txHash
	if eventData.VerificationData == "" || eventData.VerificationData == "0x" {
		inboundMsg.VerificationData = txHashHex
	} else {
		inboundMsg.VerificationData = eventData.VerificationData
	}
```

**File:** test/integration/uexecutor/inbound_cea_gas_and_payload_test.go (L210-244)
```go
	t.Run("isCEA=true uses recipient directly for GAS_AND_PAYLOAD without factory lookup by sender", func(t *testing.T) {
		chainApp, ctx, vals, _, coreVals, ueaAddrHex := setupInboundCEAGasAndPayloadTest(t, 4)
		usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr

		// A different sender that has no deployed UEA
		differentSender := utils.GetDefaultAddresses().TargetAddr2

		validUP := &uexecutortypes.UniversalPayload{
			To:                   ueaAddrHex.String(),
			Value:                "1000000",
			Data:                 "0xa9059cbb000000000000000000000000527f3692f5c53cfa83f7689885995606f93b616400000000000000000000000000000000000000000000000000000000000f4240",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		}

		ceaInbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xceagas02",
			Sender:           differentSender,
			Recipient:        ueaAddrHex.String(),
			Amount:           "1000000",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_GAS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: differentSender,
			},
		}
```

**File:** test/integration/uexecutor/inbound_cea_payload_test.go (L568-612)
```go
	t.Run("PRC20 balance lands at explicitly passed recipient even when recipient is not the sender's UEA", func(t *testing.T) {
		// Setup deploys a UEA for testAddress (person A).
		// This test sends an inbound whose Sender is TargetAddr2 (person B, no UEA deployed).
		// Recipient is person A's UEA — a UEA that has no relation to person B.
		// After execution the PRC20 balance must be at the recipient (person A's UEA), proving
		// that CEA routing is driven purely by the explicit recipient field, not by the sender's identity.
		prc20ABI, err := uexecutortypes.ParsePRC20ABI()
		require.NoError(t, err)
		prc20Address := utils.GetDefaultAddresses().PRC20USDCAddr

		chainApp, ctx, vals, _, coreVals, ueaAddrHex := setupInboundCEAPayloadTest(t, 4)
		usdcAddress := utils.GetDefaultAddresses().ExternalUSDCAddr
		ueModuleAccAddress, _ := chainApp.UexecutorKeeper.GetUeModuleAddress(ctx)

		// person B — a sender that has no deployed UEA
		personBSender := utils.GetDefaultAddresses().TargetAddr2

		validUP := &uexecutortypes.UniversalPayload{
			To:                   ueaAddrHex.String(),
			Value:                "1000000",
			Data:                 "0xa9059cbb000000000000000000000000527f3692f5c53cfa83f7689885995606f93b616400000000000000000000000000000000000000000000000000000000000f4240",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		}

		ceaInbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xcea07",
			Sender:           personBSender, // person B — no UEA
			Recipient:        ueaAddrHex.String(), // person A's UEA
			Amount:           "1000000",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: personBSender,
			},
		}
```
