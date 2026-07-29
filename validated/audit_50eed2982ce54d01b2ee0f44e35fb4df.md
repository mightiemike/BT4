## Finding

### Title
Universal Executor Account "isCEA" inbound execution swallows real bridged funds on deposit failure with no revert path — analogous to `RewardsDistributor.claim()`'s early-return fund loss ([File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
Just as the Alchemix `RewardsDistributor.claim()` bug lets an early `return alcxAmount` bypass the `msg.value` check and permanently strand attached ETH, Push Chain's `isCEA` inbound-execution branches take an execution path that, on failure, deliberately skips the `INBOUND_REVERT` outbound creation that would otherwise return the user's bridged value to the source chain. The result is a real, externally-locked/burned asset (already confirmed by honest Universal Validator quorum) that is never minted on Push Chain and never refunded on the source chain.

### Finding Description
`ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload` both branch on `utx.InboundTx.IsCEA`. In the non-`isCEA` branch, any deposit failure sets `shouldRevert = true`, which later triggers `buildRevertOutbound` + `attachOutboundsToUtx` so the user's funds are returned on the source chain: [1](#0-0) 

In the `isCEA` branch, however, failures (invalid recipient format, factory `isUEA` check failure, or `depositPRC20` failure) only set `execErr` — `shouldRevert` is never set to `true` in that code path: [2](#0-1) 

The comment even documents the intent: "isCEA failures never create an INBOUND_REVERT outbound," and downstream, on `execErr != nil`, the function just returns `nil` without any refund: [3](#0-2) 

The same pattern repeats in the GAS_AND_PAYLOAD flow, where an `isCEA` failure records a `FAILED` PCTx and returns `nil` with no revert: [4](#0-3) 

Since the `UniversalTx` model treats `PcTx`/`OutboundTx` as the only source of truth for "what happened" (per the module README), a `FAILED` PcTx with no attached `OutboundTx` leaves the UTX in a terminal, unrecoverable state — the deposit never happened on Push Chain, and no compensating outbound was ever queued for UVs to relay back to the source chain: [5](#0-4) 

The `IsCEA` and `Recipient` fields of the `Inbound` come from the UV's decoding of the real, honestly-observed source-chain gateway event — i.e., they reflect exactly what the (potentially malicious) depositing user specified in their transaction. An attacker who is depositing their own funds can freely choose a `Recipient` value that is a valid-looking hex address but is neither a deployed UEA nor a contract implementing `executeUniversalTx` correctly, or that maps to an unregistered/removed token config, guaranteeing `execErr != nil` on the isCEA path while `shouldRevert` stays `false`.

### Impact Explanation
This falls squarely into "permanent freezing of funds" — the in-scope impact category. Real value that was locked/burned on the external chain (validated by honest UV quorum via `MsgVoteInbound`) is neither minted to any Push Chain address nor returned via a revert outbound. The funds are permanently stuck, mirroring the ETH-swallowing behavior in the original Alchemix report, except triggered by an ordinary crosschain deposit path rather than a payable Solidity function.

### Likelihood Explanation
Likelihood is low-to-medium: it requires the depositing user to set `IsCEA=true` semantics with a `Recipient` that fails downstream validation/deposit (e.g., non-UEA/non-contract address, or asset lacking a `NativeRepresentation.ContractAddress`). This is plausible either as user error (mistyped/unsupported recipient) or as a self-inflicted attack where a user deliberately forfeits their own bridged funds to prove the bug — the same "medium, low-likelihood but high-impact" characterization the original report used for Alchemix.

### Recommendation
Treat isCEA deposit failures the same as non-isCEA failures for the purpose of fund safety: set `shouldRevert = true` (or otherwise always schedule an `INBOUND_REVERT` outbound) whenever the deposit/mint step itself fails, even on the isCEA path. If the isCEA design intentionally excludes revert only for payload-execution failures *after* a successful deposit, the deposit-failure branches must be separated from post-deposit payload failures so pre-deposit / deposit failures always trigger a refund path.

### Proof of Concept
1. On an external chain, deposit real funds via the gateway with `TxType_FUNDS_AND_PAYLOAD` and `IsCEA=true`, setting `Recipient` to an address that is neither a deployed UEA nor a contract exposing `executeUniversalTx` (or to an `AssetAddr` whose token config has since been removed via `RemoveTokenConfig`).
2. Honest UVs observe this real deposit and submit `MsgVoteInbound` until quorum, exactly as in `test/integration/uexecutor/vote_inbound_validation_test.go`'s "non-isCEA FUNDS inbound deposit failure DOES create revert outbound" test — but flip `IsCEA=true`. [6](#0-5) 
3. `ExecuteInboundFundsAndPayload` hits the isCEA branch, `depositPRC20`/factory check fails, `execErr != nil`, but `shouldRevert` remains `false`.
4. Assert: `utx.PcTx` contains a `FAILED` entry, and `utx.OutboundTx` is empty — no `INBOUND_REVERT` was ever created, confirming the externally-bridged funds are unrecoverable.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L53-103)
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
			} else {
				// Non-UEA: check if recipient has code (smart contract) vs EOA
				codeHash := k.evmKeeper.GetCodeHash(sdkCtx, ueaAddr)
				if codeHash != types.EmptyCodeHash && codeHash != (common.Hash{}) {
					// Smart contract: will call executeUniversalTx after deposit
					isSmartContract = true
				}
				// EOA: just deposit, skip executeUniversalTx (no contract to call)
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
			}
		}
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L187-206)
```go
	// If deposit failed, stop here.
	if execErr != nil {
		if shouldRevert {
			revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)
			if attachErr := k.attachOutboundsToUtx(
				sdkCtx,
				universalTxKey,
				[]*types.OutboundTx{revertOutbound},
				revertReason,
			); attachErr != nil {
				if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
					u.RevertError = attachErr.Error()
					return nil
				}); storeErr != nil {
					return storeErr
				}
			}
		}
		return nil
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L211-214)
```go
	// isCEA failures: record FAILED PCTx but no revert
	if execErr != nil && utx.InboundTx.IsCEA {
		return nil
	}
```

**File:** x/uexecutor/README.md (L138-148)
```markdown
### Status is derived from component state, not stored

The current `UniversalTx` record has **no status field at all**. Field `5` is reserved precisely because the old `UniversalTxStatus` enum field was removed in favour of computing status on the fly from the underlying components. This avoids the staleness class of bugs where a stored status gets out of sync with the actual outbounds/PC txs after a partial update.

Instead, callers ask "what's the state of this UTX?" by inspecting:

- whether `OutboundTx[]` is non-empty, and the per-entry `outbound_status` (`PENDING` / `OBSERVED` / `REVERTED` / `ABORTED`)
- whether `PcTx[]` is non-empty, and each entry's `status` string (`"SUCCESS"` / `"FAILED"`)
- whether `InboundTx` is set

The priority for any rollup view is **outbounds > PC txs > inbound presence**: as soon as an outbound exists, the UTX is "in the outbound phase" regardless of how the PC txs went; before that, PC tx state dominates; before that, the UTX is just a recorded inbound waiting to be executed.
```

**File:** test/integration/uexecutor/vote_inbound_validation_test.go (L312-341)
```go
	t.Run("non-isCEA FUNDS inbound deposit failure DOES create revert outbound", func(t *testing.T) {
		chainApp, ctx, vals, coreVals, _ := setupInboundValidationTest(t, 4)

		// non-isCEA FUNDS inbound
		inbound := &uexecutortypes.Inbound{
			SourceChain: "eip155:11155111",
			TxHash:      "0xrevert01",
			Sender:      testAddress,
			Recipient:   testAddress,
			Amount:      "1000000",
			AssetAddr:   usdcAddress.String(),
			LogIndex:    "1",
			TxType:      uexecutortypes.TxType_FUNDS,
			RevertInstructions: &uexecutortypes.RevertInstructions{
				FundRecipient: testAddress,
			},
		}

		// Remove token config to force the deposit step to fail
		chainApp.UregistryKeeper.RemoveTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)

		// Reach quorum
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}
```
