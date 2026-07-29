### Title
RemoveTokenConfig permanently strands already-burned PRC20 outbound funds because `BuildOutboundsFromReceipt` cannot create the outbound and there is no revert/rescue path for outbound-side token config lookup failures - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
When a user withdraws funds from Push Chain via the `UniversalGatewayPC` contract, the withdraw call burns/locks the user's PRC20 balance and emits a `UniversalTxOutboundEvent` in the same EVM transaction, *before* the Cosmos-layer `BuildOutboundsFromReceipt` keeper function is invoked to build the corresponding `OutboundTx` record. That function resolves the destination-chain asset via `k.uregistryKeeper.GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)` [1](#0-0) . If the admin has removed the `TokenConfig` for that (chain, token) pair via `MsgRemoveTokenConfig`/`RemoveTokenConfig` between the time the user's PRC20 was burned and the time the outbound is built, this lookup returns `collections.ErrNotFound` and `BuildOutboundsFromReceipt` returns `nil, err` — **no `OutboundTx` is ever created** for funds that have already left the user's balance.

This is the same invariant break as the GaugeController bug: an admin-driven removal of a registry entry (gauge / token config) that a user has already "committed" value to (a vote / a burn) leaves the user's already-spent value with no on-chain path to reclaim it, because the removal path was not designed to reconcile in-flight user state referencing the removed entry.

### Finding Description
The withdraw flow works like this:
1. User calls `UniversalGatewayPC.withdraw(...)` (or `withdrawAndExecute`), which burns/pulls the user's PRC20 and emits `UniversalTxOutboundEvent(target, amount, token=PRC20 addr, chainId, ...)`.
2. The Push Chain executor processes the receipt in `BuildOutboundsFromReceipt`, decoding the event and resolving the real external asset address by looking up the PRC20 in the token registry: `k.uregistryKeeper.GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)` [1](#0-0) .
3. If that lookup fails (`err != nil`), the function returns immediately with an error and **no `OutboundTx` is appended** — contrast this with the *inbound* path, where a missing/removed token config produces a `FAILED` `PCTx` and the executor still auto-builds and attaches an `INBOUND_REVERT` outbound to refund the user (see `handleFailedInboundValidation` and `buildRevertOutbound`, which explicitly schedule a revert path on failure) [2](#0-1) .
4. There is no equivalent "outbound revert/rescue" mechanism for a failure occurring at `BuildOutboundsFromReceipt` time. The rescue path (`AttachRescueOutboundFromReceipt`) only exists for CEA/inbound deposit failures where the *inbound* PCTx failed or an `INBOUND_REVERT` outbound was already `REVERTED` [3](#0-2)  — it is not wired to recover from a failed outbound-creation event caused by a removed token config on the *withdraw* (outbound) side.
5. `RemoveTokenConfig` itself performs no check for whether the token is referenced by any pending/in-flight withdrawal, and simply deletes the row (and its PRC20 index entry) unconditionally: `k.TokenConfigs.Remove(ctx, storageKey)` [4](#0-3) , confirmed by the index test showing removal instantly makes `GetTokenConfigByPRC20` return `ErrNotFound` [5](#0-4) .

This precisely mirrors the GaugeController analog: a legitimate governance/admin action (`RemoveTokenConfig`) removes a registry entry that an honest, unprivileged user already committed value to (burned PRC20 for a withdrawal), and the protocol's own guard rails ("Can only vote/withdraw for a valid config") end up permanently orphaning the user's already-spent value with no recovery mechanism, exactly as `vote_for_gauge_weights` blocked Alice's ability to reclaim voting power once G1 was removed.

### Impact Explanation
A user who initiates a withdrawal (burning PRC20 in the same transaction that emits the outbound event) can have their funds permanently and unrecoverably lost if the admin removes the corresponding `TokenConfig` in the window between the withdraw transaction being included and `BuildOutboundsFromReceipt` processing that receipt. Since PRC20 has already been burned/pulled by the gateway contract, and no `OutboundTx` record is created (so there's nothing in `PendingOutbounds` for UVs to sign/settle, and no automatic revert/rescue path triggers), the user has no way to either receive the destination-chain asset or get the PRC20 back. This is unauthorized/unrecoverable loss of user funds, matching the "permanent loss" category in the allowed-impact gate.

### Likelihood Explanation
This requires the admin to remove a `TokenConfig` for a token, which is a legitimate, expected operational action (e.g. deprecating a token or migrating pools) rather than a malicious act — the same non-adversarial trigger condition as the original GaugeController H-08 finding. Any user with an in-flight withdrawal transaction for that token at the moment of removal is affected. Because token config removal and user withdrawal transactions are asynchronous and there's no atomicity/reservation mechanism preventing this race, this is realistically triggerable in production whenever an admin retires a token that still has pending/outstanding balances.

### Recommendation
- Before allowing `RemoveTokenConfig` to succeed, require the token to have zero outstanding PRC20 supply issued and no pending inbounds/outbounds referencing it (or otherwise gate removal so it cannot happen while the token is still withdrawable/in circulation).
- Alternatively/additionally, make `BuildOutboundsFromReceipt` resilient to a missing `TokenConfig`: instead of returning an error and dropping the outbound entirely, create the `OutboundTx` in an `ABORTED` state (the `Status_ABORTED` value already exists for "requires manual intervention" cases) so an admin escape hatch (analogous to `RevertStuckInbound`) can be built to refund or re-route the already-burned funds.
- Add an explicit outbound-side "rescue" flow mirroring `AttachRescueOutboundFromReceipt`/`RevertStuckInbound` that can be triggered when `BuildOutboundsFromReceipt` fails due to a token-config lookup failure, so admins have a canonical, auditable recovery path instead of silently losing the outbound record.

### Proof of Concept
Conceptual reproduction (integration-test style, following the existing `TestInboundGas`/`TestSolanaInboundFunds` "missing token config" pattern already used elsewhere in the test suite [6](#0-5) , but applied to the withdraw/outbound path instead of inbound):

1. Register a chain + token config with a PRC20 native representation, and mint/deposit PRC20 into a user's UEA.
2. Have the user's UEA call `UniversalGatewayPC.withdraw(...)`, burning their PRC20 and emitting a `UniversalTxOutboundEvent` referencing that PRC20 address.
3. Before the executor keeper processes the receipt (e.g. call `chainApp.UregistryKeeper.RemoveTokenConfig(ctx, destChain, tokenAddr)` right after the withdraw tx and before `BuildOutboundsFromReceipt` runs, simulating a same-block or same-epoch admin removal).
4. Call `k.BuildOutboundsFromReceipt(ctx, utxId, receipt)` and observe it returns an error and an empty `outbounds` slice — i.e., `require.Error(t, err)` and `require.Empty(t, outbounds)`.
5. Verify no `OutboundTx` exists in `PendingOutbounds` for this event, and that the user's PRC20 was already burned on Push Chain (balance reduced) with no compensating credit anywhere — demonstrating the permanent loss.

Note: I was not able to directly inspect the Solidity/production `UniversalGatewayPC.withdraw()` implementation itself (only the test-only stub in `test/utils/contracts_setup.go`, which explicitly does not perform real burning) [7](#0-6) , so the exact production-contract burn-then-emit ordering could not be fully confirmed from the indexed code; a Devin session with full repository access would be needed to verify the real gateway contract's burn/emit sequencing before treating this as fully confirmed.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-67)
```go
		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-261)
```go
		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
		} else {
			hasRevertedAutoRevert := false
			for _, ob := range originalUtx.OutboundTx {
				if ob != nil && ob.TxType == types.TxType_INBOUND_REVERT && ob.OutboundStatus == types.Status_REVERTED {
					hasRevertedAutoRevert = true
					break
				}
			}
			if !hasRevertedAutoRevert {
				return fmt.Errorf("rescue: UTX %s has no reverted inbound-revert outbound", originalUtxId)
			}
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

**File:** x/uregistry/keeper/msg_remove_token_config.go (L10-28)
```go
// RemoveTokenConfig removes an existing token configuration in the uregistry.
func (k Keeper) RemoveTokenConfig(ctx context.Context, chain, tokenAddress string) error {
	storageKey := types.GetTokenConfigsStorageKey(chain, tokenAddress)

	// Check if the token config exists
	if has, err := k.TokenConfigs.Has(ctx, storageKey); err != nil {
		return err
	} else if !has {
		return fmt.Errorf("token config for %s on chain %s does not exist", tokenAddress, chain)
	}

	if err := k.TokenConfigs.Remove(ctx, storageKey); err != nil {
		return err
	}
	k.Logger().Info("token config removed",
		"chain", chain,
		"token_address", tokenAddress,
	)
	return nil
```

**File:** x/uregistry/keeper/prc20_index_test.go (L198-215)
```go
// TestGetTokenConfigByPRC20_RemoveDropsIndexEntry: removing a TokenConfig
// must drop its PRC20Index entry (otherwise lookup would return a non-existent
// primary key).
func TestGetTokenConfigByPRC20_RemoveDropsIndexEntry(t *testing.T) {
	ctx, k, _ := setupPRC20Keeper(t)

	key := types.GetTokenConfigsStorageKey(tcChainA, "0xUSDC")
	require.NoError(t, k.TokenConfigs.Set(ctx, key, makeTokenCfg(tcChainA, "0xUSDC", "0xPRC20")))

	got, err := k.GetTokenConfigByPRC20(ctx, tcChainA, "0xPRC20")
	require.NoError(t, err)
	require.Equal(t, "0xUSDC", got.Address)

	require.NoError(t, k.TokenConfigs.Remove(ctx, key))

	_, err = k.GetTokenConfigByPRC20(ctx, tcChainA, "0xPRC20")
	require.ErrorIs(t, err, collections.ErrNotFound, "remove must drop the index entry")
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

**File:** test/utils/contracts_setup.go (L325-346)
```go
// ---------------------------------------------------------------------------------------
// NOTE: The UniversalGatewayPC contract deployed here is a TEST-ONLY version.
//
// The withdraw() and withdrawAndExecute() functions inside this test contract:
//
//   - DO NOT run validation (_validateCommon)
//   - DO NOT compute gas fees via UniversalCore
//   - DO NOT pull PRC20 fees into VaultPC
//   - DO NOT burn PRC20 tokens
//   - DO NOT interact with any external contracts
//
// Instead, both functions simply **emit UniversalTxWithdraw with hardcoded values**:
//
//	chainId   = "eip155:11155111"
//	gasToken  = fixed test address
//	gasFee    = 111
//
// This behavior is intentional because Cosmos integration tests only need to verify:
//   - ABI correctness
//   - Event emission structure
//   - Outbound pipeline handling
//   - UE/UEM processing logic on the Cosmos side
```
