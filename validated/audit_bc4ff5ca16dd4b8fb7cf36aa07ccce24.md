Based on my research, I found a plausible analog to the bad-debt/partial-seizure write-off gap, but it comes with a caveat about contract-side behavior I could not fully verify in this repo (the `RescueFundsOnSourceChain` PRC20-burn logic lives in the `UniversalGatewayPC` Solidity contract, which is out of the Go-code index).

### Title
Failed RESCUE_FUNDS outbound permanently burns/loses PRC20 with no re-mint path - (File: x/uexecutor/keeper/outbound.go)

### Summary
`handleFailedOutbound` only re-mints bridged PRC20 back to the user for outbounds whose `TxType` is `FUNDS`, `GAS_AND_PAYLOAD`, or `FUNDS_AND_PAYLOAD`. `TxType_RESCUE_FUNDS` is not included in that re-mint set, so if a rescue outbound is observed as failed on the destination chain by honest validators, the underlying value is neither returned to the user's PRC20 balance on Push Chain nor delivered externally — the outbound is simply marked `REVERTED` and the excess gas refund logic runs, but the principal is left unaccounted for.

### Finding Description
`FinalizeOutbound` dispatches to `handleFailedOutbound` when honest Universal Validators vote `success=false` via `MsgVoteOutbound` for any outbound, including `RESCUE_FUNDS`. [1](#0-0) 

Inside `handleFailedOutbound`, the re-mint (bridged-fund recovery) branch is gated on a `TxType` allow-list that excludes `RESCUE_FUNDS`: [2](#0-1) 

`RESCUE_FUNDS` outbounds are created as the last-resort recovery path when a `CEA` deposit failed (funds never minted) or a normal `INBOUND_REVERT` outbound already failed to return funds — i.e., they represent the final attempt to make a user whole for funds already stuck/burned on the source-chain/gateway side. [3](#0-2) 

The event that spawns a `RESCUE_FUNDS` outbound, `RescueFundsOnSourceChain`, is emitted by the `UniversalGatewayPC` contract with a `PRC20` field, strongly implying the contract burns/locks the corresponding PRC20 balance at emission time (consistent with how `FUNDS`-type outbounds burn PRC20 on withdrawal, mirrored by `CallPRC20Deposit` re-minting on their failure). Because the RESCUE_FUNDS branch is excluded from the re-mint logic, an honestly-observed failure on the destination chain leaves that burned/locked value unrecovered, with the outbound simply transitioning to `REVERTED`. [4](#0-3) 

The code does support creating a second `RESCUE_FUNDS` outbound after the first is `REVERTED` (test confirms this), but that path requires the on-chain event `RescueFundsOnSourceChain` to fire again, which — if the PRC20 was already burned/consumed by the first rescue attempt — has nothing left to rescue. [5](#0-4) 

This mirrors the INIT Capital pattern precisely: an asset is irreversibly consumed (seized share / burned PRC20) during a "recovery/liquidation-style" action, the counter-flow (repay debt / re-mint funds) is only partially wired up, and the remaining shortfall becomes permanently unaccounted for with no protocol-level function to write it off or make the user whole.

### Impact Explanation
Permanent loss of user-owned PRC20/native value with no path to donate, socialize, or otherwise reconcile the corresponding UTX or `totalAssets`-style accounting: the `OutboundTx` record simply reads `REVERTED` while the associated funds (already burned/locked on the source-chain gateway per the rescue mechanism) vanish. This falls squarely under "permanent loss ... of user or protocol-controlled funds" and "corruption of PRC20 or native asset accounting" in the allowed-impact gate.

### Likelihood Explanation
The trigger requires only ordinary, unprivileged conditions already reachable by an honest user whose deposit failed twice (once on inbound, once on `INBOUND_REVERT`) and who then relies on the `RESCUE_FUNDS` fallback, followed by an honestly-observed destination-chain execution failure (e.g., transient gas/RPC/relayer issue) that the Universal Validators vote as `success=false`. No malicious validator, admin, or privileged actor is required — this is a straightforward honest-failure path through `MsgVoteOutbound`. [6](#0-5) 

### Recommendation
Add `types.TxType_RESCUE_FUNDS` to the re-mint condition in `handleFailedOutbound` (mirroring `FUNDS`/`GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`) so a failed rescue outbound re-credits the recipient's PRC20 balance via `CallPRC20Deposit`, exactly as other fund-carrying outbound types do. If the intent is instead that the PRC20 has genuinely not been burned yet at rescue-outbound-creation time (only burned upon a *successful* destination delivery), that invariant needs to be explicitly verified against the `UniversalGatewayPC` contract and, if true, this finding does not apply — but the current Go-side code gives no indication that RESCUE_FUNDS failures are otherwise reconciled.

### Proof of Concept
1. A CEA inbound deposit fails (unregistered/misconfigured asset), producing a UTX with `PcTx[0].Status == "FAILED"` — reachable by any user simply depositing an asset the destination contract can't accept, as shown in `setupRescueFundsTest`. [7](#0-6) 
2. The `UniversalGatewayPC` contract's rescue mechanism is invoked (this presumably burns/locks the stuck PRC20 and emits `RescueFundsOnSourceChain`), and `AttachRescueOutboundFromReceipt` attaches a `PENDING` `RESCUE_FUNDS` outbound to the original UTX. [8](#0-7) 
3. Honest Universal Validators reach quorum voting `success=false` on `MsgVoteOutbound` for this outbound (e.g., a transient destination-chain revert).
4. `FinalizeOutbound` → `handleFailedOutbound` runs; because `TxType_RESCUE_FUNDS` is not in the funds-revert `TxType` set, `CallPRC20Deposit` is never invoked, and the outbound is marked `REVERTED` with no re-minted balance — the user's original deposit value is unrecoverable through this flow. [9](#0-8) 

**Uncertainty note:** I was unable to inspect the `UniversalGatewayPC` Solidity contract's `rescueFunds`/burn implementation (it is outside this Go-code repository's index), so I cannot fully confirm the exact point at which PRC20 is burned relative to outbound success/failure. This assumption is based on the naming convention, the `PRC20` field on the emitted event, and the symmetry with how other outbound types burn-on-send and re-mint-on-failure. If a Devin session/full-repo review shows the PRC20 burn only happens after a *successful* rescue delivery is confirmed, this finding would not hold and should be re-evaluated against the actual contract code.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L71-97)
```go
func (k Keeper) FinalizeOutbound(ctx context.Context, utxId string, outbound types.OutboundTx) error {
	// If not observed yet, do nothing
	if outbound.OutboundStatus != types.Status_OBSERVED {
		return nil
	}

	obs := outbound.ObservedTx
	if obs == nil {
		return nil
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Info("finalizing outbound",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"success", obs.Success,
		"dest_chain", outbound.DestinationChain,
		"tx_type", outbound.TxType.String(),
	)

	if !obs.Success {
		return k.handleFailedOutbound(sdkCtx, utxId, outbound, obs)
	}

	return k.handleSuccessfulOutbound(sdkCtx, utxId, outbound, obs)
}
```

**File:** x/uexecutor/keeper/outbound.go (L102-160)
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

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}

	outbound.OutboundStatus = types.Status_REVERTED
	k.Logger().Info("outbound reverted",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)

	// Refund excess gas regardless of tx type — gas was consumed on the external
	// chain whether the execution succeeded or failed.
	k.applyGasRefund(ctx, &outbound, obs)

	return k.UpdateOutbound(ctx, utxId, outbound)
```

**File:** x/uexecutor/keeper/create_outbound.go (L238-262)
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
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L303-333)
```go
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

**File:** test/integration/uexecutor/rescue_funds_test.go (L74-140)
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
}
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L391-449)
```go
	t.Run("rescue can be retried after previous rescue is REVERTED", func(t *testing.T) {
		chainApp, ctx, vals, utxId, coreVals := setupRescueFundsTest(t, 4)

		// Grant authz for outbound voting
		for i, val := range coreVals {
			accAddr, err := sdk.ValAddressFromBech32(val.OperatorAddress)
			require.NoError(t, err)
			coreAcc := sdk.AccAddress(accAddr)
			uniAcc := sdk.MustAccAddressFromBech32(vals[i])
			auth := authz.NewGenericAuthorization(sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}))
			exp := ctx.BlockTime().Add(time.Hour)
			err = chainApp.AuthzKeeper.SaveGrant(ctx, uniAcc, coreAcc, auth, &exp)
			require.NoError(t, err)
		}

		// First rescue outbound
		log1 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err := chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx07a", log1), uexecutortypes.PCTx{TxHash: "0xrescuetx07a", Status: "SUCCESS"})
		require.NoError(t, err)

		utx, _, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		rescueOb := findRescueOutbound(utx)
		require.NotNil(t, rescueOb)

		// Vote to reach quorum with FAILURE → status becomes REVERTED
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreAcc := sdk.AccAddress(valAddr).String()
			err = utils.ExecVoteOutbound(t, ctx, chainApp, vals[i], coreAcc, utxId, rescueOb, false, "rescue failed", rescueOb.GasFee)
			require.NoError(t, err)
		}

		utx, _, err = chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		require.Equal(t, uexecutortypes.Status_REVERTED, findRescueOutbound(utx).OutboundStatus)

		// Second rescue is now allowed since the first is REVERTED
		log2 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err = chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx07b", log2), uexecutortypes.PCTx{TxHash: "0xrescuetx07b", Status: "SUCCESS"})
		require.NoError(t, err)

		utx, _, err = chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)

		// Two rescue outbounds exist: first REVERTED, second PENDING
		var rescueObs []*uexecutortypes.OutboundTx
		for _, ob := range utx.OutboundTx {
			if ob != nil && ob.TxType == uexecutortypes.TxType_RESCUE_FUNDS {
				rescueObs = append(rescueObs, ob)
			}
		}
		require.Len(t, rescueObs, 2, "two rescue outbounds expected after retry")
		require.Equal(t, uexecutortypes.Status_REVERTED, rescueObs[0].OutboundStatus)
		require.Equal(t, uexecutortypes.Status_PENDING, rescueObs[1].OutboundStatus)
	})
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L110-147)
```go
	// Step 5: Update outbound state to OBSERVED
	outbound.OutboundStatus = types.Status_OBSERVED
	outbound.ObservedTx = &observedTx

	k.Logger().Info("outbound observed",
		"utx_id", utxId,
		"outbound_id", outboundId,
		"success", observedTx.Success,
		"dest_chain", outbound.DestinationChain,
	)

	// Persist the state inside UniversalTx
	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Remove from pending outbounds index now that status is OBSERVED
	if err := k.PendingOutbounds.Remove(ctx, outboundId); err != nil {
		return fmt.Errorf("failed to remove pending outbound index for %s: %w", outboundId, err)
	}

	// Step 6: Finalize outbound (refund if failed).
	// If re-mint fails, handleFailedOutbound marks it ABORTED internally and returns nil.
	// Business logic errors are stored in RevertError on the UTX; only infra errors are returned.
	if err := k.FinalizeOutbound(ctx, utxId, outbound); err != nil {
		k.Logger().Error("outbound finalization error stored on utx",
			"utx_id", utxId,
			"outbound_id", outboundId,
			"error", err.Error(),
		)
		if storeErr := k.UpdateUniversalTx(ctx, utxId, func(u *types.UniversalTx) error {
			u.RevertError = err.Error()
			return nil
		}); storeErr != nil {
			return storeErr
		}
	}

```
