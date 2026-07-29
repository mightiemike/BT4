### Title
Missing CEA Guard in `ExecuteInboundGas` Allows a Failed Gas-Only CEA Inbound to Spawn Both an `INBOUND_REVERT` Outbound and Remain Eligible for `RESCUE_FUNDS`, Enabling Duplicate Release of the Same Locked Source-Chain Funds — (File: `x/uexecutor/keeper/execute_inbound_gas.go`)

### Summary
For every other CEA-eligible inbound handler (`execute_inbound_funds.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`) the code explicitly skips auto-creating an `INBOUND_REVERT` outbound when `inbound.IsCEA == true`, relying instead on the separate `RESCUE_FUNDS` flow (gated by `AttachRescueOutboundFromReceipt` in `x/uexecutor/keeper/create_outbound.go`) as the sole path to return funds still locked on the source chain. `ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go`) has no such `!inbound.IsCEA` check before calling `buildRevertOutbound`/`attachOutboundsToUtx` on failure.

### Finding Description
`ExecuteInboundGas` unconditionally creates a revert outbound whenever `execErr != nil && shouldRevert` [1](#0-0) , regardless of `inbound.IsCEA`. Compare this with the sibling handler for funds deposits, which explicitly gates revert-outbound creation on `!inbound.IsCEA`: [2](#0-1) .

The `x/uexecutor/README.md` documents this asymmetry as intentional design: CEA failures are supposed to be recoverable only through the operator-triggered `RESCUE_FUNDS` mechanism, not the automatic `INBOUND_REVERT` path, because CEA recipients/flows are handled differently from standard UEA flows [3](#0-2) .

Separately, `AttachRescueOutboundFromReceipt` determines rescue eligibility purely from whether `PcTx[0].Status == "FAILED"` for CEA inbounds [4](#0-3) , and its duplicate-outbound guard only inspects existing `TxType_RESCUE_FUNDS` outbounds, not `TxType_INBOUND_REVERT` ones [5](#0-4) . Because `ExecuteInboundGas` does not exempt CEA inbounds from auto-revert creation, a failed CEA `TxType_GAS` inbound can end up with both: (1) an auto-generated `INBOUND_REVERT` outbound created synchronously at execution time, and (2) later eligibility for a `RESCUE_FUNDS` outbound once an admin/relayer submits the corresponding `RescueFundsOnSourceChain` receipt — since the rescue eligibility check only inspects `PcTx[0].Status`, not whether an `INBOUND_REVERT` already exists or was already honored.

### Impact Explanation
If both outbounds (`INBOUND_REVERT` and `RESCUE_FUNDS`) are independently signed and broadcast by TSS to the source-chain gateway for the same underlying locked deposit, this could result in the same escrowed/vaulted source-chain balance being released twice — an unauthorized double-refund of user or protocol-controlled funds, directly matching the "unauthorized release" and "loss of funds" impact categories in scope. This mirrors the reported bug class (funds sitting in a contract with an incomplete/inconsistent recovery path), but here the analog is a Push Chain accounting/state-machine inconsistency rather than a missing function: two independent, differently-gated recovery mechanisms can both fire for the same failed UTX.

### Likelihood Explanation
This requires: (a) an inbound of `TxType_GAS` (gas-abstraction inbound) marked `IsCEA=true` that is crafted (or naturally occurs) to fail during `ExecuteInboundGas` (e.g., quoter/fee-tier/deposit failure), and (b) the corresponding `RescueFundsOnSourceChain` event later being emitted/observed on the source-chain gateway. I was not able to fully confirm within the available context whether `TxType_GAS` inbounds are validated/permitted to carry `IsCEA=true` upstream (e.g., in `msg_vote_inbound.go` / `handle_failed_inbound_validation.go`), nor whether the source-chain gateway itself has an independent guard preventing a second release against an already-reverted deposit. Given the intentional and consistent `!inbound.IsCEA` guard present in the three sibling handlers but absent here, this looks like an overlooked case rather than deliberate design, but confirming actual on-chain double-release requires tracing the gateway-side vault/escrow accounting for a given `universalTxId`, which was outside what I could verify with the tools available.

### Recommendation
Add the same `!inbound.IsCEA` guard used in `execute_inbound_funds.go`, `execute_inbound_funds_and_payload.go`, and `execute_inbound_gas_and_payload.go` to `ExecuteInboundGas` before calling `buildRevertOutbound`/`attachOutboundsToUtx`, so that CEA failures for gas-only inbounds are exclusively resolved via the `RESCUE_FUNDS` flow. Additionally, harden `AttachRescueOutboundFromReceipt`'s duplicate-guard to also reject rescue if an `INBOUND_REVERT` outbound already exists (in any non-failed terminal state) for the same UTX, closing the gap even if another code path regresses this invariant in the future.

### Proof of Concept
1. An external-chain sender submits a cross-chain gas-abstraction deposit (`TxType_GAS`) with `IsCEA=true` and an `AssetAddr` that has no registered token config on Push Chain (mirrors the pattern used in `setupRescueFundsTest`, see [6](#0-5) , but with `IsCEA=true` and `TxType_GAS`).
2. Universal Validators vote the inbound to quorum; `ExecuteInboundGas` fails at `GetTokenConfig` (step 1), sets `shouldRevert=true`, and — because there is no `!inbound.IsCEA` check — calls `attachOutboundsToUtx` with an `INBOUND_REVERT` outbound [1](#0-0) .
3. TSS signs and broadcasts the `INBOUND_REVERT` outbound to the source-chain gateway, releasing the locked deposit back to the sender.
4. Independently, an operator/relayer later submits a `RescueFundsOnSourceChain` receipt for the same `universalTxId`. `AttachRescueOutboundFromReceipt` only checks `PcTx[0].Status == "FAILED"` (true here) and that no active `RESCUE_FUNDS` outbound exists (true, since the first recovery was an `INBOUND_REVERT`, not `RESCUE_FUNDS`) [7](#0-6) , so a second, independent outbound is attached and can be signed/broadcast for the same original deposit.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L192-208)
```go
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

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
```

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

**File:** x/uexecutor/README.md (L271-282)
```markdown
  per outbound indicate validator divergence on the destination-chain
  observation (different `success`/`tx_hash`/`error_msg`/`gas_fee_used`).
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-282)
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

		k.Logger().Info("rescue outbound detected",
			"original_utx_id", originalUtxId,
			"pc_tx_hash", receipt.Hash,
		)

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

**File:** test/integration/uexecutor/rescue_funds_test.go (L90-121)
```go
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
```
