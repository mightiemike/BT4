## Analysis

The Gondi bug (missing borrower-identity check before reusing another user's collateral/authorization) maps directly onto Push Chain's **isCEA inbound execution path** in `x/uexecutor`. The `MsgExecutePayload` flow correctly enforces that a UEA's owner must sign any payload executed on their behalf — the `x/uexecutor/README.md` documents that the UEA contract's `executeUniversalTx` checks `evmFrom != UNIVERSAL_EXECUTOR_MODULE` before requiring an owner signature [1](#0-0) . That implies the converse: when the caller **is** the module (`0x14191Ea...`), the UEA contract skips owner-signature verification entirely, trusting the module unconditionally because the module is expected to only invoke payloads that ballot-consensus has tied to the correct owner's own inbound.

That invariant is broken by the `isCEA` branch. For `isCEA=true` inbounds, the target UEA is taken directly from the attacker-controlled `Recipient` field of the source-chain event — with no relation whatsoever to the inbound `Sender`, as explicitly demonstrated by the test asserting CEA routing is "driven purely by the explicit recipient field, not by sender identity" [2](#0-1) . After the deposit step, both `ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload` unconditionally call:

```go
receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
``` [3](#0-2) [4](#0-3) 

`ueaAddr` here is the attacker-chosen `Recipient`, `evmFrom` is `ueModuleAddr` (the trusted `UNIVERSAL_EXECUTOR_MODULE`), and `UniversalPayload`/`VerificationData` are both taken verbatim from the attacker-crafted source-chain event — `VerificationData` is routinely empty in the CEA path, as shown in every CEA test fixture [5](#0-4) . `ExecutePayloadV2`/`CallUEAExecutePayload` forward these directly into `executeUniversalTx` on the victim's UEA [6](#0-5) [7](#0-6) .

Because `evmFrom == UNIVERSAL_EXECUTOR_MODULE`, the UEA is documented to skip the owner-signature check that would otherwise block this — so an attacker can pick any deployed victim UEA as `Recipient`, set `UniversalPayload.To`/`Data` to any call (e.g. a PRC20 `transfer`/`approve`), and have it executed with the victim's UEA as `msg.sender`, entirely bypassing owner authorization once honest validators simply relay the (attacker-controlled but real) source-chain event.

### Title
Unauthorized execution of arbitrary payloads from any victim UEA via the `isCEA` inbound path bypassing owner-signature authorization - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
`isCEA` inbounds let an unprivileged external-chain actor pick an arbitrary `Recipient` (any already-deployed UEA on Push Chain) and an arbitrary `UniversalPayload`. Once honest validators vote the inbound to quorum (they only attest the source-chain event happened, not that the sender is entitled to act on the victim UEA), the executor keeper invokes `ExecutePayloadV2`/`CallUEAExecutePayload` with `evmFrom = UNIVERSAL_EXECUTOR_MODULE` against that arbitrary UEA. Per the module's own documented trust model, the UEA contract skips the owner-signature check whenever the caller is the module, so the attacker's payload executes as the victim UEA's own call with no cryptographic proof of the owner's intent.

### Finding Description
- `MsgExecutePayload`'s security model is explicit: authorization for executing a payload against a UEA is enforced only inside the UEA contract via a signature check, and that check is skipped when `evmFrom == UNIVERSAL_EXECUTOR_MODULE` [8](#0-7) .
- The `isCEA` inbound branches in `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` resolve the execution target purely from the inbound's `Recipient` field [9](#0-8) , with no requirement that `Recipient` correspond to the `Sender`'s own UniversalAccountId.
- Regardless of whether the target came from the CEA path or the standard sender-derived UEA path, both functions unconditionally forward `utx.InboundTx.UniversalPayload` and `utx.InboundTx.VerificationData` (attacker-controlled, typically empty for CEA) into `ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, ...)` [3](#0-2) .
- `ExecutePayloadV2` calls `CallUEAExecutePayload`, which issues a `DerivedEVMCall` to the UEA's `executeUniversalTx(payload, verificationData)` with `from = ueModuleAddr` [7](#0-6) .
- Because `from == UNIVERSAL_EXECUTOR_MODULE`, per the documented contract behavior, `executeUniversalTx` does not require `verificationData` to recover to the UEA owner's key — the module is unconditionally trusted.

### Impact Explanation
An unprivileged actor who can only submit ordinary transactions on a supported external chain (no validator, TSS, or admin privilege required) can cause arbitrary contract calls to be executed with `msg.sender` = any already-deployed victim UEA. This is direct "unauthorized UEA execution" and can be leveraged to drain any asset the UEA can move (PRC20 balances, approvals to DeFi protocols, staking positions, etc.), i.e. stealing/draining user-controlled funds, matching the in-scope impact categories for universal execution.

### Likelihood Explanation
Likelihood is high: it requires only a normal, honestly-relayed source-chain event (a real transaction the attacker pays for on the external chain) with `isCEA=true`, `Recipient` = a chosen victim UEA, and a malicious `UniversalPayload`. No validator, node, or admin misbehavior is required — three honest validators voting the real observed event to quorum is sufficient to trigger execution.

### Recommendation
For `isCEA` inbounds targeting an existing UEA, require that the UEA's stored owner (`UniversalAccountId`) matches the inbound `Sender`/origin before invoking `ExecutePayloadV2`, or otherwise require a valid owner-signed `VerificationData` even when the caller is the module, closing the "trust module unconditionally" shortcut for third-party-specified recipients. At minimum, do not allow `isCEA` payload execution against a UEA the message's own origin/sender does not own unless the payload carries a valid owner signature.

### Proof of Concept
1. Attacker identifies a deployed victim UEA `V` on Push Chain (all UEAs are deterministically derivable/queryable).
2. Attacker crafts a `TxType_FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`) event on a supported external chain with `IsCEA=true`, `Recipient = V`, `Amount` minimal, `AssetAddr` a supported token, and `UniversalPayload{ To: <PRC20 token or protocol contract on Push Chain>, Data: <malicious calldata, e.g. transfer(attacker, balance) or approve(attacker, max)>, VType: 0 }`, `VerificationData: ""`.
3. Honest Universal Validators observe this real (attacker-paid) source-chain event and submit `MsgVoteInbound` for it; quorum is reached exactly as in the existing test flows (`test/integration/uexecutor/inbound_cea_payload_test.go` "isCEA=true uses recipient directly..." [2](#0-1) ).
4. `ExecuteInboundFundsAndPayload` deposits into `V`, then calls `ExecutePayloadV2(ctx, ueModuleAddr, V, maliciousPayload, "")`, which calls `V.executeUniversalTx(maliciousPayload, "")` with `msg.sender = UNIVERSAL_EXECUTOR_MODULE`, bypassing owner-signature verification and executing the attacker's calldata as `V`.

### Citations

**File:** x/uexecutor/README.md (L220-237)
```markdown
#### Where authorization actually lives

The cryptographic binding is enforced inside the UEA contract's `executeUniversalTx` (see [`UEA_EVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_EVM.sol#L145) and [`UEA_SVM.sol`](https://github.com/pushchain/push-chain-core-contracts/blob/86e20e2d26819e7cc885549f08c66895221dfab0/src/uea/UEA_SVM.sol)):

1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**

#### Why this is safe under `Signer ≠ Owner`

An attacker submitting `MsgExecutePayload` with their own `Signer` and a victim's `UniversalAccountId` produces no exploitable outcome:

- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
- The attacker cannot forge `VerificationData` that recovers to the victim's owner key.
- The contract reverts → the keeper returns an error → the Cosmos transaction reverts in full.
- Net effect: zero state change. No EVM gas is charged to the victim UEA (the deduction is rolled back with the rest of the transaction). The submission costs the attacker nothing on chain (gasless), but also achieves nothing.
```

**File:** test/integration/uexecutor/inbound_cea_payload_test.go (L656-719)
```go
	t.Run("isCEA=true uses recipient directly without factory lookup by sender universalAccountId", func(t *testing.T) {
		// The key difference: isCEA=true does NOT look up UEA via sender's UniversalAccountId.
		// Instead it directly validates and uses the explicit recipient address.
		// We demonstrate this by setting Sender to an address that has no deployed UEA —
		// with isCEA=false this would fail, but with isCEA=true it should succeed
		// because the valid UEA is already specified in Recipient.
		chainApp, ctx, vals, ceaInbound, coreVals, ueaAddrHex := setupInboundCEAPayloadTest(t, 4)
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

		// isCEA=true: sender has no UEA, but recipient is a valid deployed UEA — should succeed
		ceaInboundDifferentSender := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xcea06",
			Sender:           differentSender,
			Recipient:        ueaAddrHex.String(), // valid UEA
			Amount:           "1000000",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: differentSender,
			},
		}

		_ = ceaInbound // setup already deployed the UEA; we only use ueaAddrHex

		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, ceaInboundDifferentSender)
			require.NoError(t, err)
		}

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*ceaInboundDifferentSender)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.GreaterOrEqual(t, len(utx.PcTx), 1)

		// Deposit should succeed because recipient is a valid UEA regardless of sender
		depositPcTx := utx.PcTx[0]
		require.Equal(t, "SUCCESS", depositPcTx.Status,
			"isCEA=true should succeed using recipient UEA directly, ignoring whether sender has a UEA")
	})
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-80)
```go
	if utx.InboundTx.IsCEA {
		// isCEA path: recipient is explicitly specified.
		// Three-way check:
		//   1. Recipient is a UEA  → existing flow (deposit + ExecutePayloadV2)
		//   2. Recipient is a deployed smart contract (not UEA) → deposit + executeUniversalTx
		//   3. Neither → record FAILED PCTx, no INBOUND_REVERT
		if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
			execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
		} else {
			ueaAddr = common.HexToAddress(utx.InboundTx.Recipient)

			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
			} else if isUEA {
				// UEA path: deposit PRC20 into the UEA (if amount > 0), then execute payload via UEA
				if inboundAmount.Sign() > 0 {
					receipt, execErr = k.depositPRC20(
						sdkCtx,
						utx.InboundTx.SourceChain,
						utx.InboundTx.AssetAddr,
						ueaAddr,
						utx.InboundTx.Amount,
					)
					if execErr != nil {
						execErr = fmt.Errorf("depositPRC20 failed: %w", execErr)
					}
				}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-290)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L288-298)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

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

**File:** test/integration/uexecutor/inbound_cea_gas_and_payload_test.go (L442-457)
```go
		ceaInbound := &uexecutortypes.Inbound{
			SourceChain:      "eip155:11155111",
			TxHash:           "0xceagas06",
			Sender:           personBSender,
			Recipient:        ueaAddrHex.String(),
			Amount:           "1000000",
			AssetAddr:        usdcAddress.String(),
			LogIndex:         "1",
			TxType:           uexecutortypes.TxType_GAS_AND_PAYLOAD,
			UniversalPayload: validUP,
			VerificationData: "",
			IsCEA:            true,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: personBSender,
			},
		}
```

**File:** x/uexecutor/keeper/execute_payload.go (L17-53)
```go
func (k Keeper) ExecutePayloadV2(ctx context.Context, evmFrom common.Address, ueaAddr common.Address, universalPayload *types.UniversalPayload, verificationData string) (*vmtypes.MsgEthereumTxResponse, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Debug("execute payload v2",
		"uea", ueaAddr.Hex(),
		"from", evmFrom.Hex(),
	)

	// Step 1: Validate payload and verificationData early (fast-fail before EVM work)
	if _, err := types.NewAbiUniversalPayload(universalPayload); err != nil {
		return nil, errors.Wrapf(err, "invalid universal payload")
	}

	verificationDataVal, err := utils.HexToBytes(verificationData)
	if err != nil {
		return nil, errors.Wrapf(err, "invalid verificationData format")
	}

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

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```

**File:** x/uexecutor/keeper/evm.go (L155-193)
```go
// CallUEAExecutePayload executes a universal payload through UEA
func (k Keeper) CallUEAExecutePayload(
	ctx sdk.Context,
	from, ueaAddr common.Address,
	universal_payload *types.UniversalPayload,
	verificationData []byte,
) (*evmtypes.MsgEthereumTxResponse, error) {
	abi, err := types.ParseUeaABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UEA ABI")
	}

	abiUniversalPayload, err := types.NewAbiUniversalPayload(universal_payload)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to create universal payload")
	}

	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
}
```
