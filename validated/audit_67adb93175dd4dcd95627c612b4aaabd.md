### Title
Deposit failures on `IsCEA` inbounds permanently strand user funds with no revert path - (File: `x/uexecutor/keeper/execute_inbound_funds.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/handle_failed_inbound_validation.go`)

### Summary
This is the same invariant class as the SafEth `stake()`-with-no-derivative bug: a user-initiated deposit is accepted and consumed on the source side while the corresponding on-chain accounting step silently no-ops, permanently trapping the value with no automatic remediation. In Push Chain's `x/uexecutor`, when an inbound is flagged `IsCEA` (cross-environment application recipient), every deposit-execution path explicitly skips creating an `INBOUND_REVERT` outbound if `depositPRC20` (or the downstream `executeUniversalTx` call) fails — the code comments literally say "isCEA failures never create an INBOUND_REVERT outbound."

### Finding Description
The mitigation pattern intentionally applied across the codebase is: for `IsCEA == true` inbounds, any deposit/execution error is only recorded as a `FAILED` PCTx entry — no revert outbound is ever scheduled to return funds on the source chain: [1](#0-0) [2](#0-1) [3](#0-2) 

The `IsCEA` flag and `AssetAddr`/`Recipient` fields on an `Inbound` originate from an external-chain gateway event that is watched and voted on by `universalClient`/validators — i.e., it is effectively attacker-controlled input from an ordinary user's own source-chain transaction (an unprivileged user chooses what gateway method to call, which asset to send, and what recipient/CEA metadata to embed). An ordinary user can:
1. Lock/deposit real funds via the source-chain gateway with `IsCEA = true` and a token/asset address that is not (or no longer) registered in `x/uregistry` — analogous to calling `stake()` with `derivativeCount == 0`.
2. Once quorum of honest validators votes the inbound in, `ExecuteInboundFunds`/`ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` call `depositPRC20`, which fails because `GetTokenConfig` can't resolve the asset (this exact failure mode — "GetTokenConfig failed" — is already proven reachable and tested for the non-CEA path): [4](#0-3) 
3. Because `inbound.IsCEA` is true, the explicit guard skips the revert-outbound creation that the non-CEA path performs. The UniversalTx is left with a `FAILED` PCTx entry and no `OutboundTx` of type `INBOUND_REVERT`, and no PRC20 is minted to any recipient.

The result mirrors the SafEth analog precisely: the value that was locked/consumed on the source chain (analogous to `msg.value`) has no corresponding mint on Push Chain, and — unlike the non-CEA path — no automatic mechanism exists to return it to the sender. The funds are permanently unaccounted for from the protocol's perspective (stuck), since `UniversalTx.PcTx` only records failure text, and there is no retry, no refund, no re-processing route visible in the reviewed code (`RescueFunds`-style tests exist for other scenarios but this dead-end path wasn't found routed to any rescue flow in the excerpts reviewed).

### Impact Explanation
This directly maps to "permanent freezing of user or protocol-controlled funds," in scope for this engagement, and is reachable by an ordinary, unprivileged user (no malicious validator, node, or admin assumption is required — honest validators simply vote in a real gateway event exactly as designed). The bug class is a direct code-level analog of the referenced SafEth finding: acceptance of value/consumption on one side, silent no-op with no refund on the state-transition side.

### Likelihood Explanation
Medium-to-high. All three execution paths (`funds`, `funds_and_payload`, `gas_and_payload`) and the generic `handleFailedInboundValidation` fallback share the identical "isCEA failures never create an INBOUND_REVERT outbound" logic, so it is a systemic, intentional design decision rather than an isolated slip — but its exact preconditions (what makes `depositPRC20`/`GetTokenConfig`/`executeUniversalTx` fail for a `CEA` inbound in practice, e.g., unregistered asset, asset later removed via `MsgRemoveTokenConfig`, or `IsCEA` combined with a nonexistent contract at the specified recipient) were not fully re-verified end-to-end from the raw source-chain gateway call through `universalClient` ingestion in this pass — I could not fully trace whether `universalClient`'s indexer pre-filters unregistered assets before submitting `MsgVoteInbound`, which would affect how easily an attacker can hit this path with only the excerpts reviewed.

### Recommendation
Apply the same treatment uniformly regardless of `IsCEA`: on deposit or downstream execution failure, always schedule an `INBOUND_REVERT` outbound (or an equivalent explicit refund/rescue mechanism) so that user funds represented by a failed inbound are never left with only a `FAILED` PCTx and no path back to the sender. If `IsCEA` inbounds are intentionally excluded from automatic reverts for a specific protocol reason, that reasoning should be documented and paired with an alternative recovery path (e.g., a permissionless "claim/rescue" message) that lets the original sender reclaim funds without requiring privileged intervention.

### Proof of Concept
Not independently executed; based on the existing integration test pattern already present in the repository for the analogous non-CEA path (`test/integration/uexecutor/execute_inbound_gas_test.go:366-403`, "GAS inbound with missing token config records FAILED PCTx and creates revert"), the same setup with `IsCEA: true` instead should be run: remove/omit the token config for the asset, submit an `IsCEA` inbound through `MsgVoteInbound` to quorum, and assert on the resulting `UniversalTx` — expected: a `FAILED` PCTx is recorded but `utx.OutboundTx` contains **no** `TxType_INBOUND_REVERT` entry, confirming funds are stranded with no recovery path.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L74-86)
```go
	// isCEA failures never create an INBOUND_REVERT outbound
	// (consistent with execute_inbound_funds_and_payload.go and execute_inbound_gas_and_payload.go)
	if err != nil && !inbound.IsCEA {
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
		if attachErr := k.attachOutboundsToUtx(sdkCtx, utx.Id, []*types.OutboundTx{revertOutbound}, err.Error()); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, utx.Id, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-65)
```go
	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
			// Store the revert failure reason on the UTX so it's queryable on-chain.
			// The FAILED PCTx is already recorded above — this adds why the revert wasn't attached.
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
				utx.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				// UpdateUniversalTx only fails on infra issues — return to roll back and retry
				return storeErr
			}
		}
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L366-403)
```go
	t.Run("GAS inbound with missing token config records FAILED PCTx and creates revert", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		inbound.TxHash = "0xgas0020"

		// Remove token config to force GetTokenConfig to fail
		chainApp.UregistryKeeper.RemoveTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "universal tx should exist even when token config is missing")

		// Must have a FAILED PCTx
		require.NotEmpty(t, utx.PcTx, "PCTx entries must be recorded")
		hasFailed := false
		for _, pcTx := range utx.PcTx {
			if pcTx.Status == "FAILED" {
				hasFailed = true
				require.Contains(t, pcTx.ErrorMsg, "GetTokenConfig failed",
					"error message should indicate token config lookup failure")
				break
			}
		}
		require.True(t, hasFailed, "should have a FAILED PCTx when token config is missing")

		// Must have an INBOUND_REVERT outbound
		foundRevert := false
		for _, ob := range utx.OutboundTx {
			if ob.TxType == uexecutortypes.TxType_INBOUND_REVERT {
				foundRevert = true
				break
			}
		}
		require.True(t, foundRevert, "INBOUND_REVERT outbound should be created when token config is missing")
	})
```
