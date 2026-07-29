Confirmed: this is a real, reachable analog of the ZkSync "default account distinguishes caller by `msg.sender`, breaking EOA-equivalence" bug class, and the test suite explicitly demonstrates it. The Push Chain UEA contract skips its owner-signature check when `msg.sender == UNIVERSAL_EXECUTOR_MODULE` (the trusted module account), and the `isCEA` inbound path lets an unprivileged attacker route an arbitrary `Recipient` UEA address into that exact trusted call path with an attacker-chosen `UniversalPayload`.

### Title
Attacker-controlled `isCEA` inbound recipient lets the trusted module-sender bypass execute arbitrary payloads against any victim UEA - ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
`x/uexecutor`'s CEA (custom execution address) inbound path lets the source-chain event itself specify an arbitrary `Recipient`. When that recipient resolves to *any* deployed UEA on Push Chain — not necessarily one belonging to the inbound's `Sender` — the module still calls `ExecutePayloadV2` → `CallUEAExecutePayload` with `from = ueModuleAddr` (the `UNIVERSAL_EXECUTOR_MODULE` address). Per the module and test documentation, the UEA contract "skips verification for module-sender calls," i.e. it distinguishes EOA-like signature-checked calls from trusted-module calls purely by `msg.sender`, exactly like ZkSync's `DefaultAccount` distinguishing bootloader calls by `msg.sender`. Because the attacker fully controls the source-chain event fields (`Recipient`, `UniversalPayload.To/Data/Value`) that Universal Validators honestly relay, this "trusted sender" special case becomes reachable from an unprivileged, ordinary deposit/gateway-event flow against a UEA the attacker doesn't own.

### Finding Description
- `ValidateForExecution` in [1](#0-0)  only requires that `Recipient` be a syntactically valid hex address when `IsCEA` is true — it never ties `Recipient` to `Sender`'s own `UniversalAccountId`.
- `ExecuteInboundFundsAndPayload` resolves `ueaAddr` directly from `utx.InboundTx.Recipient` for the `isCEA` branch and, if that address is a deployed UEA (via `CallFactoryGetOriginForUEA`), deposits funds then executes the payload through it: [2](#0-1) 
- The payload execution call is made with `from = ueModuleAddr` (the uexecutor module account), not the recipient/owner's derived EVM address: [3](#0-2) 
- The integration test explicitly documents and exploits this asymmetric-caller behavior: "The UEA's executeUniversalTx skips verification for module-sender calls" [4](#0-3) 
- The README confirms the design intent that signature enforcement in the UEA is conditional on `evmFrom != UNIVERSAL_EXECUTOR_MODULE`: [5](#0-4) 
- An existing integration test (`"PRC20 balance lands at explicitly passed recipient even when recipient is not the sender's UEA"`) already proves that the `Recipient` used for the trusted-module call can belong to a completely unrelated party (person A's UEA) while `Sender` is person B: [6](#0-5) 

Put together: an attacker triggers a real gateway deposit event on any registered external chain (an ordinary, unprivileged user action), setting `Sender = attacker`, `Recipient = victim's already-deployed UEA address`, and `UniversalPayload = {To: <target>, Data: <arbitrary calldata>, Value: <arbitrary>}`. Honest Universal Validators faithfully observe and vote this real event via `MsgVoteInbound` (they are not being asked to lie — the event genuinely occurred with these attacker-chosen fields). Once quorum is reached, the core validator executes `ExecuteInboundFundsAndPayload`, which classifies `Recipient` as a valid UEA and calls `ExecutePayloadV2(ctx, ueModuleAddr, victimUEA, attackerPayload, "")` — with `VerificationData` empty/attacker-controlled and irrelevant, since the UEA contract's signature check is skipped precisely because `msg.sender == UNIVERSAL_EXECUTOR_MODULE`. This lets the attacker execute arbitrary calldata as the victim's UEA (e.g., calling `transfer`/`approve` on the victim's PRC20 holdings, or invoking `migrateUEA`/other privileged UEA methods) without ever possessing the victim's private key or a valid signature.

### Impact Explanation
This breaks the core "contract-only binding" authorization invariant described in the module's own README (`executeUniversalTx` should only execute payloads whose signature recovers to the UEA owner). Here, the "trusted module, no verification needed" fast-path — intended only for the case where the module is relaying the *same* Sender's own pre-validated inbound payload — is reachable with a `Recipient`/target UEA that has no relationship to `Sender`. An unprivileged attacker can therefore drive arbitrary EVM calls (fund transfers, approvals, migrations) against any victim's UEA, i.e. unauthorized UEA execution and potential draining/loss of user-controlled PRC20/native funds — squarely in the "unauthorized UEA execution" and "unauthorized state transitions" allowed-impact categories.

### Likelihood Explanation
High reachability: triggering an inbound with a chosen `Recipient` only requires the attacker to emit a real, valid gateway event on any registered external chain (a normal deposit-like action), which honest UVs will relay as-is. No privileged role, validator collusion, or key compromise is required — the entire attack path is "ordinary user submits a crafted but real cross-chain deposit."

### Recommendation
Enforce that, for `isCEA` inbounds, the trusted module-sender payload-execution fast path is only used when the resolved `Recipient` UEA's owner matches the inbound's `Sender`-derived `UniversalAccountId` (i.e., re-introduce the binding the non-CEA path already provides via `CallFactoryToGetUEAAddressForOrigin`). Alternatively, require genuine `VerificationData`/signature checking in the UEA contract for CEA-routed payloads regardless of `msg.sender`, so the module-sender bypass can only ever apply to a UEA's own inbound, never to an arbitrary third-party UEA supplied via `Recipient`.

### Proof of Concept
1. Attacker registers/uses a normal external-chain account and deploys/uses UEA infrastructure normally (no privilege needed).
2. Attacker performs a real gateway deposit on a registered external chain (e.g., calls `addFunds`), embedding calldata such that the resulting `Inbound` has: `Sender = attacker`, `IsCEA = true`, `Recipient = <victim's deployed UEA address>`, `TxType = FUNDS_AND_PAYLOAD`, `UniversalPayload = {To: victimUEA's PRC20 token, Data: transfer(attacker, victimBalance), Value: 0}`.
3. Honest Universal Validators observe this real event and vote it via `MsgVoteInbound`; quorum is reached exactly as in [7](#0-6) .
4. `ExecuteInboundFundsAndPayload` resolves `Recipient` as a valid deployed UEA ( [8](#0-7) ) and calls `ExecutePayloadV2(ctx, ueModuleAddr, victimUEA, attackerPayload, "")` ( [3](#0-2) ).
5. Because `from == UNIVERSAL_EXECUTOR_MODULE`, the victim UEA's `executeUniversalTx` skips owner-signature verification (per [4](#0-3)  and [5](#0-4) ) and executes the attacker's `transfer` call, moving the victim's PRC20 balance to the attacker.

Note: the exact Solidity-level `msg.sender`-gated skip logic lives in `UEA_EVM.sol` in the separate `push-chain-core-contracts` repository, which is outside this repo's index — I could not directly inspect that condition's source, but its existence and behavior are corroborated by this repo's own README and integration-test comments cited above.

### Citations

**File:** x/uexecutor/types/inbound.go (L152-164)
```go
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
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

**File:** test/integration/uexecutor/vote_inbound_validation_test.go (L126-131)
```go
		// Construct a FUNDS_AND_PAYLOAD inbound whose payload will revert at EVM execution.
		// The UEA's executeUniversalTx skips verification for module-sender calls, so we
		// must trigger a revert in the execution step itself.
		// Strategy: call the Handler contract with an invalid function selector — the
		// Handler has no fallback, so the low-level .call() returns success=false and
		// the UEA reverts with ExecutionFailed().
```

**File:** x/uexecutor/README.md (L233-234)
```markdown
- The factory resolves the victim's UEA address from the embedded `UniversalAccountId` — correct.
- `evmFrom` (derived from `Signer`) becomes the EVM-level `msg.sender` of the call to the UEA. Since `evmFrom != UNIVERSAL_EXECUTOR_MODULE` (`0x14191Ea54B4c176fCf86f51b0FAc7CB1E71Df7d7`), the contract enforces the signature check.
```

**File:** test/integration/uexecutor/inbound_cea_payload_test.go (L163-184)
```go
	t.Run("quorum reached executes inbound when isCEA is true and recipient is valid UEA", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals, _ := setupInboundCEAPayloadTest(t, 4)

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
		require.True(t, found, "universal tx should exist after quorum is reached")
		require.NotEmpty(t, utx.PcTx, "PcTx entries should be recorded")
	})
```

**File:** test/integration/uexecutor/inbound_cea_payload_test.go (L568-653)
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

		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, ceaInbound)
			require.NoError(t, err)
		}

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*ceaInbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)
		require.GreaterOrEqual(t, len(utx.PcTx), 1)

		depositPcTx := utx.PcTx[0]
		require.Equal(t, "SUCCESS", depositPcTx.Status,
			"deposit should succeed: recipient is a valid UEA regardless of sender identity")

		// Confirm the PRC20 balance landed at the explicitly passed recipient (person A's UEA)
		res, err := chainApp.EVMKeeper.CallEVM(
			ctx,
			prc20ABI,
			ueModuleAccAddress,
			prc20Address,
			false,
			nil,
			"balanceOf",
			ueaAddrHex,
		)
		require.NoError(t, err)

		balances, err := prc20ABI.Unpack("balanceOf", res.Ret)
		require.NoError(t, err)
		require.Len(t, balances, 1)

		expectedAmount := new(big.Int)
		expectedAmount.SetString(ceaInbound.Amount, 10)
		require.Equal(t, 0, balances[0].(*big.Int).Cmp(expectedAmount),
			"PRC20 balance must be at the explicitly passed recipient (person A's UEA), not at sender's address")
```
