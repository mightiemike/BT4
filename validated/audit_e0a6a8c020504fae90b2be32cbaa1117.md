### Title
`AttachRescueOutboundFromReceipt()` single-pending-rescue gate is DoS-able by any `MsgExecutePayload` caller — ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`x/uexecutor/keeper/create_outbound.go`'s rescue-outbound attachment logic enforces the same "one pending item, strict gate, no override" pattern as the reported `submitStrategy()` bug: it allows at most one active (`PENDING`/`OBSERVED`) `RESCUE_FUNDS` outbound per `UniversalTx`, and rejects any further attempt with a hard error while one is active. [1](#0-0) 
Crucially, this check is reached from `ExecutePayload`, which backs `MsgExecutePayload` — a message any account may submit gaslessly, with no authorization tying the caller to the UTX owner or an admin role. [2](#0-1) [3](#0-2) 

### Finding Description
`ExecutePayload` unconditionally runs `AttachRescueOutboundFromReceipt` on the EVM receipt produced by every `MsgExecutePayload` call, scanning for a `RescueFundsOnSourceChain` log emitted by the gateway/UEA contract during that call. [4](#0-3) 
When such a log is present, the keeper resolves the target `UniversalTx` by `UniversalTxId` embedded in the log, validates rescue eligibility (deposit failed for CEA, or auto-revert reverted for non-CEA), then checks whether that UTX already has an active `RESCUE_FUNDS` outbound in `PENDING` or `OBSERVED` status — if so, the entire submission is rejected. [5](#0-4) 

This mirrors the `submitStrategy()` bug-class exactly: a single mutable "pending slot" per key (here, per UTX), gated by strict on-chain state (`PENDING`/`OBSERVED`), and reachable through a permissionless entry point (`MsgExecutePayload`, "any account may submit"). [3](#0-2) 
The Cosmos-layer keeper itself performs no check that the `Signer`/`evmFrom` of the triggering `MsgExecutePayload` is privileged or matches an admin role for that UTX — the design note in the README states the rescue path is intended to be "admin-driven," but that authorization, if any, lives entirely inside the off-chain UEA/gateway Solidity contract that emits `RescueFundsOnSourceChain`, not in the Cosmos keeper path shown here. [6](#0-5) 

Whether this is exploitable by a fully unprivileged attacker therefore hinges on a piece of code (the UEA/gateway contract's emission conditions for `RescueFundsOnSourceChain`) that is **not in the indexed Go repository** and could not be verified with the available tools — the index only exposes the ABI/log-decoding side (`x/uexecutor/types/gateway_pc_event_decode.go`, `types/abi.go`) and not the Solidity source that decides who can trigger the emission. If that contract permits the log to be emitted by an unprivileged path (e.g., any UEA owner triggering their own legitimate rescue attempt, or a re-entrant/duplicate emission within the same or a crafted payload), the Cosmos-side guard's failure mode is a hard revert of the whole `MsgExecutePayload` transaction rather than an ignorable/queued attempt, which:
- Blocks a legitimate rescue attempt if an earlier (possibly attacker-triggered or race-won) rescue outbound for the same UTX is already `PENDING`, until that first rescue resolves to `OBSERVED`/`REVERTED` via TSS+UV voting — an indeterminate delay outside the caller's control (analogous to the report's "wait 6 hours or governance step in").
- Since `MsgExecutePayload` is gasless, the cost of taking the "pending slot" for a given UTX is close to zero for whoever can trigger the emission first, enabling frontrunning of a legitimate rescue submission for the same UTX.

### Impact Explanation
If the emitting condition is not strictly access-controlled at the contract layer, an attacker can occupy the single rescue slot for a victim's stuck-funds UTX, delaying or indefinitely blocking recovery of funds legitimately owed to that UTX's sender — a denial of the "unauthorized ... refund" recovery path for real user funds, which falls within the allowed impact scope (permanent freezing / unauthorized refund blocking of user-controlled funds). Because it's gated on-chain per UTX and gasless, the actual value at risk (frozen recoverable funds) can be significant per affected UTX.

### Likelihood Explanation
Likelihood is **uncertain/Medium** and directly gated on information not available in this repository: whether the UEA/gateway contract's rescue-emission function is admin-only, or reachable by any UEA owner/attacker. Given the README explicitly frames `RESCUE_FUNDS` as "Admin-driven rescue path," the contract-side gate is likely restrictive, in which case this would collapse to an intra-privileged-actor issue and fall out of the "unprivileged external attacker" scope. Without visibility into the Solidity contract, this cannot be confirmed as exploitable by a fully unprivileged party.

### Recommendation
- Verify (in the UEA/gateway Solidity source, not indexed here) that `RescueFundsOnSourceChain` can only be emitted by an authorized (admin/governance) caller for a given UTX; if so, this finding does not apply to unprivileged attackers and should be downgraded/rejected.
- If the emission is not strictly gated at the contract level, mirror the `submitStrategy()` fix: either allow queued/multiple rescue attempts per UTX (only accepting the first successfully-observed one) instead of hard-rejecting concurrent attempts, or require the Cosmos-layer `ExecutePayload`/`AttachRescueOutboundFromReceipt` path to independently authenticate that the `evmFrom`/`Signer` is authorized for rescue actions on that specific UTX before honoring the log.

### Proof of Concept
Not constructible with available tooling — reproducing the attack requires the UEA/gateway contract bytecode/source to determine what conditions cause it to emit `RescueFundsOnSourceChain`, which is outside the indexed Go repository. The Go-side control-flow enabling the DoS pattern is demonstrated by:
1. `ExecutePayload` (any caller, gasless) → `AttachRescueOutboundFromReceipt` on every call's receipt. [4](#0-3) 
2. Existing test coverage already demonstrates the single-slot rejection behavior for two rescue attempts on the same UTX (`"second rescue is rejected when first is PENDING"`, `"second rescue is rejected when first is OBSERVED"`), confirming the hard-reject invariant exists exactly as described. [7](#0-6) [8](#0-7) 

Due to index size limits, the UEA/gateway Solidity contract source that would confirm or refute unprivileged reachability of the rescue-log emission was not available; a Devin session with full repository/contract access would be needed to close this gap.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L220-282)
```go
		event, err := types.DecodeRescueFundsOnSourceChainFromLog(lg)
		if err != nil {
			return fmt.Errorf("failed to decode RescueFundsOnSourceChain: %w", err)
		}

		// The universalTxId in the event is a 0x-prefixed bytes32 matching our UTX key.
		originalUtxId := strings.TrimPrefix(event.UniversalTxId, "0x")

		originalUtx, found, err := k.GetUniversalTx(ctx, originalUtxId)
		if err != nil {
			return fmt.Errorf("rescue: failed to fetch UTX %s: %w", originalUtxId, err)
		}
		if !found {
			return fmt.Errorf("rescue: original UTX %s not found", originalUtxId)
		}
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

**File:** x/uexecutor/README.md (L136-136)
```markdown
| `RESCUE_FUNDS` | Admin-driven rescue path for stuck funds. | Outbound that delivers the rescue. |
```

**File:** x/uexecutor/README.md (L215-215)
```markdown
- **`Signer`** identifies the Cosmos transaction signer — the party that delivers the owner's pre-authorized payload to Push Chain. `MsgExecutePayload` is a gasless message type (see `app/txpolicy/gasless.go`), so the signer pays no Cosmos transaction fee. Any account may submit the message.
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L327-342)
```go
	t.Run("second rescue is rejected when first is PENDING", func(t *testing.T) {
		chainApp, ctx, _, utxId, _ := setupRescueFundsTest(t, 4)

		// First rescue — succeeds
		log1 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err := chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx05a", log1), uexecutortypes.PCTx{TxHash: "0xrescuetx05a", Status: "SUCCESS"})
		require.NoError(t, err)

		// Second rescue — rejected because first is PENDING
		log2 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err = chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx05b", log2), uexecutortypes.PCTx{TxHash: "0xrescuetx05b", Status: "SUCCESS"})
		require.Error(t, err)
		require.Contains(t, err.Error(), "already has an active rescue outbound")
	})
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L344-389)
```go
	t.Run("second rescue is rejected when first is OBSERVED", func(t *testing.T) {
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

		// Attach first rescue outbound
		log1 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err := chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx06a", log1), uexecutortypes.PCTx{TxHash: "0xrescuetx06a", Status: "SUCCESS"})
		require.NoError(t, err)

		utx, _, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		rescueOb := findRescueOutbound(utx)
		require.NotNil(t, rescueOb)

		// Vote to reach quorum with success → status becomes OBSERVED
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreAcc := sdk.AccAddress(valAddr).String()
			err = utils.ExecVoteOutbound(t, ctx, chainApp, vals[i], coreAcc, utxId, rescueOb, true, "", rescueOb.GasFee)
			require.NoError(t, err)
		}

		utx, _, err = chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		require.Equal(t, uexecutortypes.Status_OBSERVED, findRescueOutbound(utx).OutboundStatus)

		// Second rescue rejected because first is OBSERVED
		log2 := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err = chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx06b", log2), uexecutortypes.PCTx{TxHash: "0xrescuetx06b", Status: "SUCCESS"})
		require.Error(t, err)
		require.Contains(t, err.Error(), "already has an active rescue outbound")
	})
```
