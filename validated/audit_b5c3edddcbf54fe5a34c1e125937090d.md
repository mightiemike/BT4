I found a concrete analog to the report's "repeated fill-then-kill grows an unbounded array" pattern in `AttachRescueOutboundFromReceipt` / `attachOutboundsToUtx` in `x/uexecutor/keeper/create_outbound.go`.

### Title
Unbounded `UniversalTx.OutboundTx` growth via repeated PENDING→REVERTED rescue-outbound cycles causes ever-growing O(n) scans and permanently degraded UTX processing - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`AttachRescueOutboundFromReceipt` appends a new `RESCUE_FUNDS` `OutboundTx` entry to `UniversalTx.OutboundTx` every time it is invoked, and only blocks a *new* rescue attempt while a prior rescue is `PENDING`/`OBSERVED` — a `REVERTED` rescue can always be retried [1](#0-0) . This mirrors the Bloom-v2 pattern where a killed `MatchOrder` is zeroed but left in the array, letting the same actor refill/re-append repeatedly and inflate an array that other code paths must fully scan.

### Finding Description
`AttachRescueOutboundFromReceipt` is invoked from the EVM post-processing hook `EVMHooks.PostTxProcessing`, which runs after **every** EVM transaction, and from `ExecutePayload` (a gasless, permissionless message) [2](#0-1) [3](#0-2) . Any unprivileged EVM caller can emit a `RescueFundsOnSourceChain` log from `UniversalGatewayPC` referencing an arbitrary `universalTxId` that is eligible for rescue (CEA deposit failed, or non-CEA auto-revert already `REVERTED`) [4](#0-3) .

The only anti-duplication guard iterates `originalUtx.OutboundTx` for an existing `RESCUE_FUNDS` entry with status `PENDING` or `OBSERVED`; a `REVERTED` rescue does not block a new one [1](#0-0) , and this is documented/expected behavior ("rescue can be retried after previous rescue is REVERTED") [5](#0-4) . Each new rescue attempt appends both a `PCTx` and an `OutboundTx` entry to the UTX via `attachOutboundsToUtx`, which is strictly append-only [6](#0-5) .

Because a `REVERTED` outbound status is UV-controlled (validators vote it via `MsgVoteOutbound` observing failure on the destination chain), and destination-chain broadcast failures are plausible/attacker-influenceable (e.g., a source-chain contract that always reverts the rescue call), an attacker can drive many rescue cycles: PENDING → (UV votes FAILURE) → REVERTED → attacker retries → PENDING → REVERTED → ... Each cycle:
- Appends a new `OutboundTx` and `PCTx` entry to the same UTX forever (no cap, no pruning) [7](#0-6) .
- Makes every future `AttachRescueOutboundFromReceipt` call for that UTX do an O(n) linear scan over the growing `OutboundTx` slice (duplicate-guard loop plus other scans like the `hasRevertedAutoRevert` check) [8](#0-7) , and every query path (`AllPendingOutbounds`, `AllUniversalTx`, upgrade backfills) that walks `utx.OutboundTx` also grows proportionally [9](#0-8) [10](#0-9) .

### Impact Explanation
Unlike Bloom-v2's `matches` array (owned by one lender, bounded practically by borrower count), Push Chain's `UniversalTx` object is a single per-inbound record that is loaded/marshaled in full on every read and write (`GetUniversalTx`, `UpdateUniversalTx`). An attacker repeating this rescue cycle against their own UTX inflates that single UTX's `OutboundTx`/`PcTx` slices without bound, increasing gas/CPU cost of every subsequent PostTxProcessing hook invocation, and any operator tooling or query that loads/iterates that UTX (`GetUniversalTx`, `AllPendingOutbounds`, upgrade migrations that walk `UniversalTx.Iterate`). This does not directly steal funds, but it is a denial-of-service vector against processing of that specific UTX and against any bulk-scan/migration code path that touches it, potentially degrading state-growth and increasing storage/marshal costs chain-wide if repeated across many UTXs.

### Likelihood Explanation
Medium. It requires (a) getting a UTX into a rescue-eligible state (CEA deposit failure, which an attacker can trigger simply by choosing an unregistered/failing asset), and (b) getting each rescue outbound to reach `REVERTED` via honest UV consensus, which is not directly attacker-controlled but is plausible for an attacker who controls the destination-chain contract/behavior for the rescue's TSS-signed transaction (they can make the destination call always fail). Because there is no per-UTX cap on rescue retries and no cooldown, a patient/motivated attacker with control over the source-chain side can generate this growth deterministically over many blocks.

### Recommendation
- Cap the number of rescue attempts per UTX (e.g., track a `rescue_attempt_count` and reject once a threshold is reached), or replace/overwrite the prior `REVERTED` rescue `OutboundTx` in place instead of appending a new entry — analogous to the Bloom-v2 fix of not leaving a zeroed/duplicate slot for repeat attempts.
- Alternatively, prune/consolidate `REVERTED` `RESCUE_FUNDS` entries so only the latest attempt is retained on the UTX, bounding `OutboundTx` length independent of retry count.
- Add an explicit size guard (defensive max-length check) on `UniversalTx.OutboundTx`/`PcTx` before appending, returning an error rather than growing unbounded.

### Proof of Concept
1. Attacker creates a CEA inbound whose deposit fails (unregistered asset address), producing a UTX eligible for rescue, as in `setupRescueFundsTest` [11](#0-10) .
2. Attacker calls the gateway's rescue path to emit `RescueFundsOnSourceChain`, invoking `AttachRescueOutboundFromReceipt`, creating rescue outbound #1 (PENDING) [12](#0-11) .
3. UVs vote FAILURE (attacker arranges destination-chain revert), moving rescue #1 to REVERTED [13](#0-12) .
4. Attacker repeats step 2–3 indefinitely; each iteration appends a new `OutboundTx` (and `PcTx`) to the same UTX, as demonstrated by the existing test showing two rescue outbounds accumulate after one retry cycle [14](#0-13) ; nothing in the code prevents repeating this N times to grow the arrays arbitrarily.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L235-262)
```go
		if originalUtx.InboundTx == nil {
			return fmt.Errorf("rescue: UTX %s has no inbound tx", originalUtxId)
		}

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

**File:** x/uexecutor/keeper/create_outbound.go (L269-282)
```go
		// Guard against duplicate rescue outbounds: reject if an active rescue
		// (PENDING or OBSERVED) already exists. A REVERTED rescue may be retried.
		for _, ob := range originalUtx.OutboundTx {
			if ob == nil || ob.TxType != types.TxType_RESCUE_FUNDS {
				continue
			}
			if ob.OutboundStatus == types.Status_PENDING || ob.OutboundStatus == types.Status_OBSERVED {
				k.Logger().Warn("rescue outbound rejected: active rescue already exists",
					"original_utx_id", originalUtxId,
					"existing_outbound_id", ob.Id,
				)
				return fmt.Errorf("rescue: UTX %s already has an active rescue outbound (%s)", originalUtxId, ob.Id)
			}
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L322-333)
```go
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

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
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

**File:** x/uexecutor/keeper/evm_hooks.go (L60-67)
```go
	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L115-121)
```go
	// Step 6: create outbound + UTX only if needed
	if err := k.CreateUniversalTxFromReceiptIfOutbound(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
	if err := k.AttachRescueOutboundFromReceipt(sdkCtx, receipt, pcTx); err != nil {
		return err
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

**File:** x/uexecutor/keeper/query_server.go (L444-460)
```go
	// Convert to pointers and resolve full outbound data
	var entries []*types.PendingOutboundEntry
	var outbounds []*types.OutboundTx
	for i := range pageEntries {
		entries = append(entries, &pageEntries[i])

		utx, err := k.UniversalTx.Get(ctx, pageEntries[i].UniversalTxId)
		if err != nil {
			continue
		}
		for _, ob := range utx.OutboundTx {
			if ob != nil && ob.Id == pageEntries[i].OutboundId {
				outbounds = append(outbounds, ob)
				break
			}
		}
	}
```

**File:** app/upgrades/ai-audit-fixes-2/upgrade.go (L104-119)
```go
		for _, ob := range utx.OutboundTx {
			if ob == nil {
				continue
			}
			if ob.OutboundStatus == uexecutortypes.Status_PENDING {
				entry := uexecutortypes.PendingOutboundEntry{
					OutboundId:    ob.Id,
					UniversalTxId: utxId,
					CreatedAt:     0, // unknown historical height
				}
				if err := keeper.PendingOutbounds.Set(ctx, ob.Id, entry); err != nil {
					return fmt.Errorf("failed to set pending outbound %s: %w", ob.Id, err)
				}
				count++
			}
		}
```
