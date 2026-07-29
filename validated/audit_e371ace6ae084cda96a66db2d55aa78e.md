### Title
CEA UEA-path payload execution bypasses `verifyTxHash` binding, letting an unprivileged attacker execute arbitrary payloads against an existing victim UEA - ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go])

### Summary
The external report's bug class is: "a caller can push work onto an account without any cryptographic proof that the account owner actually authorized it." In Push Chain's CEA (isCEA=true) inbound-payload flow, when the explicit `Recipient` resolves to an already-deployed UEA, the keeper skips the `verifyTxHash`-style binding that ties a payload to a specific observed source-chain transaction (sender + source chain + tx id), and instead executes the payload straight through `ExecutePayloadV2` with the `uexecutor` module account as `evmFrom`.

### Finding Description
For `IsCEA=true` inbounds of type `FUNDS_AND_PAYLOAD` / `GAS_AND_PAYLOAD`, execution branches on whether `Recipient` is a UEA or a generic smart contract:
- Non-UEA smart-contract recipient → `k.CallExecuteUniversalTx(cacheCtx, ueaAddr, utx.InboundTx.SourceChain, []byte(utx.InboundTx.Sender), payload, amount, prc20Addr, txId)` [1](#0-0) . This path passes `SourceChain`, `Sender`, and `txId` into the EVM call, which is exactly the shape the `cea-payload-verification-fix` upgrade describes as feeding the `verifyTxHash` precompile check inside the UEA contract [2](#0-1) .
- UEA recipient (the "existing UEA" branch) → the deposit happens, then later the code falls through to the generic `k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)` call, the *same* call used for ordinary (non-CEA) inbound execution [3](#0-2) . This call does **not** pass `SourceChain`, `Sender`, or `txId` at all — there is no tx-hash binding for this branch.

Per `x/uexecutor/README.md`, `MsgExecutePayload`/`ExecutePayloadV2`'s only authorization guarantee is that the UEA contract checks a signature recovered from `VerificationData` against the UEA's stored owner key, *unless* `evmFrom == UNIVERSAL_EXECUTOR_MODULE`, in which case the module is implicitly trusted and the contract does not enforce the signature check [4](#0-3) . In the CEA/UEA-recipient branch, `ExecutePayloadV2` is invoked with `ueModuleAddr` (the module account) as `evmFrom` and `VerificationData` taken directly from the attacker-controlled `Inbound.VerificationData` field of the vote, which integration tests show is routinely empty (`""`) for CEA inbounds [5](#0-4) . Since the module-account sender path is exempt from the UEA contract's signature check, and there is no tx-hash/sender binding equivalent to the smart-contract branch, the only "proof" that this payload was authorized by the UEA's real owner is the CAIP-2 `Recipient` field of the `Inbound` struct — a field fully controlled by whichever party crafts the source-chain event that Universal Validators subsequently vote on. Confirming test coverage shows this is explicit, intended behavior: "isCEA=true should succeed using recipient UEA directly, ignoring whether sender has a UEA" [6](#0-5) .

This is structurally identical to the reported bug: a front-end/back-end split where one side (the source-chain event / `hello`-style enrolment) claims to add security via a "signature," but the actual privileged operation (execution against the target account) is performed by a component (the snap backend / here, the module-as-sender EVM call) that never verifies the claim.

### Impact Explanation
If validated, an unprivileged attacker who can emit *any* real, honestly-observed source-chain gateway event (e.g., a small ERC20 transfer they legitimately execute themselves) with `isCEA=true`, an arbitrary `UniversalPayload`, and `Recipient` set to any already-deployed victim UEA, can get Push Chain to call `executeUniversalTx` against the victim's UEA from the trusted module account — with no signature from the victim and no tx-hash binding proving the victim authorized that specific payload. Because the UEA contract does not enforce the owner-signature check for the module-account sender, an attacker-chosen `UniversalPayload.data` (arbitrary calldata, arbitrary `to`) could be executed with the victim UEA as `msg.sender`, which can drain/move funds or state controlled by the victim's UEA — this maps directly to the "unauthorized UEA execution" / "unauthorized state transitions in universal execution flows" impact category.

### Likelihood Explanation
Reachability depends entirely on unprivileged, honest-validator-observed input: the attacker only needs to trigger a genuine source-chain event (their own transaction) that the UV network faithfully reports with `isCEA=true`, `Recipient=<victim UEA>`, and a payload of their choosing. No validator collusion or privileged access is required — this fits the "unprivileged external attacker" and "honest validators" scope explicitly. The likelihood is high assuming the analysis of the missing tx-hash binding in this branch is correct.

### Recommendation
For the UEA-recipient CEA branch, require the same `verifyTxHash`-style binding used in the smart-contract branch (source chain + real sender + tx id) before calling `ExecutePayloadV2`/`executeUniversalTx`, or otherwise require that the payload's declared owner (`UniversalAccountId.Owner`) match `utx.InboundTx.Sender` and be verified against a signature/tx-hash proof, so that the module-account-as-sender exemption from the UEA's signature check is only used when a bona fide chain-level proof of the owner's authorization has been checked by the keeper itself (not merely by trusting `Recipient`).

### Proof of Concept
Not independently executed against a running node; this is derived from static tracing of the two CEA execution branches and the module-account signature bypass documented in `x/uexecutor/README.md`, cross-checked against the `TestInboundCEAFundsAndPayload`/`TestInboundCEAGasAndPayload` integration tests that explicitly assert the UEA branch succeeds "ignoring whether sender has a UEA," with `VerificationData` empty. I was not able to fully confirm the internal Solidity logic of `UEA_EVM.sol`/`executeUniversalTx` for the module-sender exemption (that contract lives in a separate `push-chain-core-contracts` repo not indexed here), so the exact bypass condition (`evmFrom == UNIVERSAL_EXECUTOR_MODULE`) is taken from the README's own description rather than verified in Solidity source.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L239-249)
```go
				cacheCtx, writeCache := sdkCtx.CacheContext()
				contractReceipt, contractErr = k.CallExecuteUniversalTx(
					cacheCtx,
					ueaAddr,
					utx.InboundTx.SourceChain,
					[]byte(utx.InboundTx.Sender),
					payload,
					amount,
					prc20Addr,
					txId,
				)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-290)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)
```

**File:** app/upgrades/cea-payload-verification-fix/upgrade.go (L39-53)
```go
		// ── Fix 1: CEA payload sender ───────────────────────────────────────────
		// In the CEA (Chain Enabled Abstraction) route, the verified payload hash
		// was stored with the inbound tx sender (CEA executor) as the sender field.
		// The UEA contract's executePayload calls the verifyTxHash precompile with
		// id.owner (UEA owner), causing a sender mismatch and verification failure.
		// Fix: store the UEA owner as the sender when isCEA=true and recipient is a UEA.
		logger.Info("Fix: CEA inbound payload hash now stores UEA owner as sender instead of inbound tx sender")

		// ── Fix 2: CEA payload chain ────────────────────────────────────────────
		// The verified payload hash was stored under the inbound source chain
		// (e.g., eip155:97), but the UEA contract calls verifyTxHash with its own
		// origin chain (e.g., eip155:11155111). This chain mismatch caused the
		// precompile lookup to fail.
		// Fix: store the payload hash under the UEA's origin chain for CEA inbounds.
		logger.Info("Fix: CEA inbound payload hash now stored under UEA origin chain instead of inbound source chain")
```

**File:** x/uexecutor/README.md (L224-237)
```markdown
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

**File:** test/integration/uexecutor/inbound_cea_gas_and_payload_test.go (L217-244)
```go
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
