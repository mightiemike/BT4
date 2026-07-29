Confirmed: `AttachRescueOutboundFromReceipt` in `x/uexecutor/keeper/create_outbound.go` never checks `IsChainOutboundEnabled` before creating a `RESCUE_FUNDS` outbound, unlike `BuildOutboundsFromReceipt`, which explicitly gates on it. This is the concrete analog to the reported bug class.### Title
Rescue-funds outbound is created without checking chain outbound-enabled state, permanently stranding user-prepaid gas fee - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
This is the closest native analog to the reported class ("value that must be returned through a specific finalization path can become permanently stuck once that path is disabled during a mass-exit/deprecation scenario"). On Push Chain, `AttachRescueOutboundFromReceipt` [1](#0-0)  attaches a `RESCUE_FUNDS` outbound to a stuck UTX whenever a user calls the rescue path on the source chain and pays an upfront `GasFee`/`GasPrice`/`GasLimit` (analogous to the report's prepaid `WITHDRAWAL_STAKE`) — but, unlike the normal outbound-creation path, it never checks whether outbound is enabled for the destination (source) chain before creating that `PENDING` outbound.

### Finding Description
The normal outbound-creation path (`BuildOutboundsFromReceipt`) explicitly guards fund movement by checking `IsChainOutboundEnabled` and rejects the outbound if it's disabled: [2](#0-1) .

`AttachRescueOutboundFromReceipt`, which handles the `RescueFundsOnSourceChain` event (a user-initiated rescue that requires the user to prepay `GasFee`/`GasPrice`/`GasLimit` on the source chain, mirroring the reported `WITHDRAWAL_STAKE` requirement to open a pending action) has no equivalent check: it fetches the original UTX, validates eligibility (deposit failed / auto-revert reverted), then unconditionally builds and attaches a `PENDING` `RESCUE_FUNDS` outbound [3](#0-2) .

`x/utss` treats "outbound disabled for a chain" as the trigger precondition for TSS key migration/deprecation of that chain: `InitiateFundMigration` requires outbound to already be disabled before it will even start migrating funds away from the old TSS key [4](#0-3) , and it separately requires all pending outbounds to have drained [5](#0-4)  via `HasPendingOutboundsForChain` [6](#0-5) . This is the Push-chain analog of "exodusMode": once outbound is disabled on a chain, that chain's TSS key is being retired and UVs will no longer be broadcasting/signing new outbound transactions for it going forward as part of the migration sequence.

If a user's inbound gets stuck (CEA deposit fails, or an auto-revert reverts) on a chain, and — after that — outbound is disabled for that chain (the operator begins deprecating/migrating the chain), the user can still call the on-chain rescue path and pay the gas-fee prepayment, and `AttachRescueOutboundFromReceipt` will happily create a new `PENDING` `RESCUE_FUNDS` outbound for it with no rejection. This outbound is indexed into `PendingOutbounds` [7](#0-6) , but because the chain's outbound is disabled (which is generally used by UVs/coordinator logic as the signal that a chain's TSS key is being decommissioned and outbound signing for it should stop), the rescue outbound can be left in a `PENDING` limbo that never resolves to `OBSERVED`. Meanwhile, `HasPendingOutboundsForChain` — which admin calls before/after `InitiateFundMigration` — would in principle detect this pending rescue outbound and block migration, but there is no code path preventing the *creation* of a new rescue outbound after outbound has already been disabled, unlike the symmetric guard present for ordinary outbound creation.

### Impact Explanation
The user's prepaid `GasFee` (the analog of `WITHDRAWAL_STAKE`) for the rescue attempt, and potentially the underlying stuck principal amount itself, has no clear path to resolution once the chain is being deprecated via the disable-outbound → migrate-funds sequence: the rescue outbound is created and left `PENDING` with no automatic detection that the destination chain's outbound is disabled, unlike the primary outbound flow that explicitly checks and rejects up front. This is a Medium-severity, portion-of-funds-lost scenario matching the report's classification.

### Likelihood Explanation
Medium: it requires (a) a stuck inbound already existing on some chain (CEA deposit failure or reverted auto-revert — both are operationally realistic, exercised by the module's own test suite [8](#0-7) ), and (b) that chain's outbound subsequently being disabled as part of a chain deprecation/TSS-key-migration event, which is an expected but infrequent operational scenario in `x/utss`.

### Recommendation
Add the same `IsChainOutboundEnabled` check used in `BuildOutboundsFromReceipt` to `AttachRescueOutboundFromReceipt` before attaching a `RESCUE_FUNDS` outbound, so rescue attempts on chains with disabled outbound are rejected (with a clear on-chain error) rather than silently creating an outbound that may never be signed/broadcast. Additionally, consider an explicit escape-hatch/refund mechanism for rescue attempts made on chains whose outbound was disabled after the rescue was already pending, so the prepaid gas fee is not stranded.

### Proof of Concept
Not independently executed; derived from static code review of the cited functions. To confirm empirically, a Devin session with repo access could: (1) drive a CEA inbound to a `FAILED` deposit state as in `setupRescueFundsTest` [9](#0-8) , (2) call `UregistryKeeper` to disable outbound for that chain, (3) call `AttachRescueOutboundFromReceipt` with a valid `RescueFundsOnSourceChain` log, and (4) assert whether a `PENDING` `RESCUE_FUNDS` outbound is created despite outbound being disabled (expected: it currently is, with no guard, versus `BuildOutboundsFromReceipt` which would reject it).

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L49-57)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L193-197)
```go
func (k Keeper) AttachRescueOutboundFromReceipt(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
```

**File:** x/uexecutor/keeper/create_outbound.go (L302-333)
```go
		logIndex := fmt.Sprintf("%d", lg.Index)
		outbound := &types.OutboundTx{
			Id:                types.GetRescueFundsOutboundId(pushChainCaip, receipt.Hash, logIndex),
			DestinationChain:  originalUtx.InboundTx.SourceChain,
			Recipient:         recipient,
			Amount:            originalUtx.InboundTx.Amount,
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.PRC20,
			Sender:            event.Sender,
			GasFee:            event.GasFee.String(),
			GasPrice:          event.GasPrice.String(),
			GasLimit:          event.GasLimit.String(),
			TxType:            types.TxType_RESCUE_FUNDS,
			OutboundStatus:    types.Status_PENDING,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: logIndex,
			},
		}

		// Record the rescue call as a PCTx on the original UTX so the full
		// PC-side history is visible (deposit FAILED → rescue call → outbound).
		if err := k.UpdateUniversalTx(ctx, originalUtxId, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &pcTx)
			return nil
		}); err != nil {
			return fmt.Errorf("rescue: failed to record PCTx on UTX %s: %w", originalUtxId, err)
		}

		if err := k.attachOutboundsToUtx(ctx, originalUtxId, []*types.OutboundTx{outbound}, ""); err != nil {
			return fmt.Errorf("rescue: failed to attach outbound to UTX %s: %w", originalUtxId, err)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L360-371)
```go
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L31-38)
```go
	// 4. Verify outbound is disabled for this chain
	outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check outbound status for chain %s: %w", chain, err)
	}
	if outboundEnabled {
		return 0, fmt.Errorf("outbound is still enabled for chain %s; disable outbound before initiating migration", chain)
	}
```

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L40-47)
```go
	// 5. Verify no pending outbounds for this chain
	hasPending, err := k.uexecutorKeeper.HasPendingOutboundsForChain(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check pending outbounds for chain %s: %w", chain, err)
	}
	if hasPending {
		return 0, fmt.Errorf("chain %s still has pending outbounds; wait for them to drain before migration", chain)
	}
```

**File:** x/uexecutor/keeper/pending_outbound_query.go (L9-34)
```go
// HasPendingOutboundsForChain checks if there are any pending outbounds for a given chain.
// It walks PendingOutbounds and joins against UniversalTx to check destination_chain.
// Returns true on first match. This is O(n) but only called during admin-initiated migration.
func (k Keeper) HasPendingOutboundsForChain(ctx context.Context, chain string) (bool, error) {
	var found bool
	err := k.PendingOutbounds.Walk(ctx, nil, func(outboundId string, entry types.PendingOutboundEntry) (bool, error) {
		utx, exists, err := k.GetUniversalTx(ctx, entry.UniversalTxId)
		if err != nil {
			return true, err
		}
		if !exists {
			return false, nil
		}
		for _, ob := range utx.OutboundTx {
			if ob.DestinationChain == chain && ob.Id == outboundId {
				found = true
				return true, nil // stop walking
			}
		}
		return false, nil
	})
	if err != nil {
		return false, err
	}
	return found, nil
}
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L74-139)
```go
// setupRescueFundsTest creates a CEA inbound whose deposit will fail (asset address has
// no registered token config), drives it to quorum, and returns the UTX key of the failed UTX.
// The returned UTX has at least one FAILED PCTx and is ready for a rescue outbound.
func setupRescueFundsTest(
	t *testing.T,
	numVals int,
) (
	*app.ChainApp,
	sdk.Context,
	[]string, // universalVals
	string, // utxId of the failed CEA UTX
	[]stakingtypes.Validator,
) {
	t.Helper()

	// Reuse the CEA environment (validators, chain/token config, authz for inbound voting).
	chainApp, ctx, vals, _, coreVals, _ := setupInboundCEAPayloadTest(t, numVals)

	testAddress := utils.GetDefaultAddresses().DefaultTestAddr
	recipient := utils.GetDefaultAddresses().TargetAddr2
	// Use an asset address that has no registered token config — depositPRC20 will fail.
	unregisteredAsset := common.HexToAddress("0x000000000000000000000000000000000000DEAD")

	inbound := &uexecutortypes.Inbound{
		SourceChain: "eip155:11155111",
		TxHash:      "0xrescue01",
		Sender:      testAddress,
		Recipient:   recipient,
		Amount:      "1000000",
		AssetAddr:   unregisteredAsset.String(),
		LogIndex:    "1",
		TxType:      uexecutortypes.TxType_FUNDS_AND_PAYLOAD,
		UniversalPayload: &uexecutortypes.UniversalPayload{
			To:                   recipient,
			Value:                "1000000",
			Data:                 "0x",
			GasLimit:             "21000000",
			MaxFeePerGas:         "1000000000",
			MaxPriorityFeePerGas: "200000000",
			Nonce:                "1",
			Deadline:             "9999999999",
			VType:                uexecutortypes.VerificationType(1),
		},
		IsCEA: true,
		RevertInstructions: &uexecutortypes.RevertInstructions{
			FundRecipient: testAddress,
		},
	}

	for i := 0; i < 3; i++ {
		valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
		require.NoError(t, err)
		coreValAcc := sdk.AccAddress(valAddr).String()
		err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], coreValAcc, inbound)
		require.NoError(t, err)
	}

	utxId := uexecutortypes.GetInboundUniversalTxKey(*inbound)
	utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
	require.NoError(t, err)
	require.True(t, found, "UTX must exist after quorum")

	require.NotEmpty(t, utx.PcTx, "setup: at least one PCTx must exist")
	require.Equal(t, "FAILED", utx.PcTx[0].Status, "setup: deposit must fail for unregistered asset")

	return chainApp, ctx, vals, utxId, coreVals
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L151-184)
```go
func TestRescueFunds(t *testing.T) {
	prc20Addr := utils.GetDefaultAddresses().PRC20USDCAddr
	senderAddr := common.HexToAddress(utils.GetDefaultAddresses().DefaultTestAddr)

	t.Run("rescue outbound is attached to original UTX on valid CEA inbound with failed deposit", func(t *testing.T) {
		chainApp, ctx, _, utxId, _ := setupRescueFundsTest(t, 4)

		log := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		receipt := makeRescueReceipt(t, "0xrescuetx01", log)
		pcTx := uexecutortypes.PCTx{TxHash: "0xrescuetx01", Status: "SUCCESS"}

		err := chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, receipt, pcTx)
		require.NoError(t, err)

		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		require.True(t, found)

		rescueObs := findRescueOutbound(utx)
		require.NotNil(t, rescueObs, "RESCUE_FUNDS outbound must be attached")
		require.Equal(t, uexecutortypes.Status_PENDING, rescueObs.OutboundStatus)
		require.Equal(t, uexecutortypes.TxType_RESCUE_FUNDS, rescueObs.TxType)
		require.Equal(t, "eip155:11155111", rescueObs.DestinationChain)
		require.Equal(t, "1000000", rescueObs.Amount)
		require.Equal(t, "111", rescueObs.GasFee)

		// The rescue call must be recorded as a PCTx in the UTX history.
		// UTX already had the failed deposit PCTx; the rescue pcTx is appended after it.
		require.Greater(t, len(utx.PcTx), 1, "rescue PCTx must be appended to UTX history")
		lastPcTx := utx.PcTx[len(utx.PcTx)-1]
		require.Equal(t, "0xrescuetx01", lastPcTx.TxHash)
		require.Equal(t, "SUCCESS", lastPcTx.Status)
	})
```
