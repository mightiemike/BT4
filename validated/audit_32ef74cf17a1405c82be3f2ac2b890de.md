## Title
Malformed or unresolvable recipient on `isCEA` inbound permanently freezes bridged funds with no revert path - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
The external report describes a case where a secondary/auxiliary lookup (`ownerOf(referrerProfileId)`) reverting blocks an otherwise-valid primary action (collecting a mirror), and the fix is to treat the missing referral as non-existent rather than block collection. Push Chain's `isCEA` inbound-execution paths contain the same class of defect, but worse: by explicit design ("isCEA failures never create an `INBOUND_REVERT` outbound"), any failure in resolving the CEA `Recipient` — including a deterministic, attacker-controlled input-format failure — results in permanently stuck bridged funds with neither delivery nor refund.

### Finding Description
In `ExecuteInboundFundsAndPayload` (`x/uexecutor/keeper/execute_inbound_funds_and_payload.go:53-103`) and the analogous `ExecuteInboundGasAndPayload` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go:61-100`), when `utx.InboundTx.IsCEA` is true, the code:

1. Requires `Recipient` to be `0x`-prefixed hex, otherwise sets `execErr` [1](#0-0) 
2. Calls `CallFactoryGetOriginForUEA` to check whether the recipient is a UEA; any error from that (analogous to `ownerOf` reverting on a burned/non-existent NFT in the Aave report) also sets `execErr` [2](#0-1) 
3. Explicitly documents and enforces: `// isCEA failures never create an INBOUND_REVERT outbound.` [3](#0-2) 

When `execErr != nil`, the function only records a `FAILED` `PCTx` entry and returns `nil` — it never builds/attaches a revert outbound (that path is gated on `shouldRevert`, which is never set to `true` for the `IsCEA` branch) [4](#0-3) .

Compare this to the non-`isCEA` sibling branch in the same function, which does set `shouldRevert = true` and builds an `INBOUND_REVERT` outbound on the exact same class of failure (factory lookup error, deploy error, deposit error) [5](#0-4) . The asymmetry means the same underlying source-chain deposit — once observed and voted on by honest validators — has a recovery path in one branch and none in the other, purely based on the `IsCEA` flag, which is attacker/sender-controlled inbound metadata.

`Recipient` for a CEA inbound is derived from raw source-chain event data (originated by the sender/CEA contract), i.e., fully attacker-controlled input reaching this code path via the normal, honest-validator vote-to-finalize pipeline (`VoteInbound` → ballot finalization → `ExecuteInboundFundsAndPayload`).

### Impact Explanation
This falls under "permanent freezing of user or protocol-controlled funds" in the allowed impact gate. Once the inbound is finalized by honest validators (funds already locked/consumed on the source chain), a malformed or unresolvable `Recipient` on an `IsCEA` inbound causes:
- No PRC20 mint / no deposit to any address on Push Chain.
- No `INBOUND_REVERT` outbound created, so no automatic refund path back to the source chain.
- The `UniversalTx` simply records a `FAILED` `PCTx` and stops, with no queued remediation.

The funds corresponding to that inbound become permanently stuck relative to the ordinary user/attacker-reachable flow, since there's no admin-independent recovery route documented for this exact case (the `RescueFunds`/`AttachRescueOutboundFromReceipt` mechanism only applies once PRC20 has already landed in the module account from a completed deposit — that never happens here because the deposit step itself is skipped when `execErr != nil`).

### Likelihood Explanation
The non-hex-prefixed `Recipient` check is a simple, deterministic, 100%-reproducible condition entirely controlled by whoever constructs the CEA inbound payload — no validator collusion or privileged action is required. If upstream `ValidateBasic`/`ValidateForExecution` do not already reject a non-hex `Recipient` before this stage for CEA-flagged inbounds, this is trivially triggerable on every affected inbound. Even if that specific format check is caught earlier, the `CallFactoryGetOriginForUEA` error branch is reachable for any recipient value the factory contract cannot resolve without reverting, and the "no revert, ever" design for `IsCEA` failures is a structural gap rather than an edge case.

### Recommendation
Mirror the non-CEA branch's graceful-degradation behavior for `IsCEA` failures that occur before any state-changing deposit: either (a) always build an `INBOUND_REVERT` outbound (refunding to `RevertInstructions.FundRecipient` / `Sender`) when a CEA recipient cannot be validated/resolved, matching the same recoverability guarantee already given to non-CEA inbounds and to Aave's own recommended fix of "treat as non-existent, don't block/lose the primary action/funds"; or (b) route unresolved-CEA-recipient failures into the existing admin `RevertStuckInbound` / rescue flow automatically rather than leaving them as a dead-end `FAILED` `PCTx`.

### Proof of Concept
1. As an unprivileged user, initiate a cross-chain deposit from a CEA contract on a supported source chain with `TxType = FUNDS_AND_PAYLOAD` (or `GAS_AND_PAYLOAD`) and `isCEA = true`.
2. Set the `Recipient` field to a value that either (a) is not `0x`-prefixed, or (b) is a valid-looking address for which `FactoryV1.getOriginForUEA` reverts/errors when called via `CallFactoryGetOriginForUEA`.
3. Let honest Universal Validators observe and vote the inbound to quorum as usual (`VoteInbound` → ballot finalized → `ExecuteInboundFundsAndPayload` invoked).
4. Observe: `execErr != nil`, a `FAILED` `PCTx` is appended to the `UniversalTx`, and the function returns `nil` without ever calling `buildRevertOutbound`/`attachOutboundsToUtx` (unlike the non-CEA branch) — confirm via `GetUniversalTx` that `utx.OutboundTx` is empty and no PRC20 was minted anywhere.
5. The originally bridged value is now unrecoverable through any user-facing or automatic protocol path.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L59-61)
```go
		if !strings.HasPrefix(strings.ToLower(utx.InboundTx.Recipient), "0x") {
			execErr = fmt.Errorf("recipient must be a valid hex address when isCEA is true")
		} else {
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L64-66)
```go
			_, isUEA, ueaCheckErr := k.CallFactoryGetOriginForUEA(sdkCtx, ueModuleAccAddress, factoryAddress, ueaAddr)
			if ueaCheckErr != nil {
				execErr = fmt.Errorf("failed to verify UEA: %w", ueaCheckErr)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L103-103)
```go
		// isCEA failures never create an INBOUND_REVERT outbound.
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L104-158)
```go
	} else {
		// Original logic: check factory for UEA, deploy if not deployed
		ueaAddrRes, isDeployed, err := k.CallFactoryToGetUEAAddressForOrigin(sdkCtx, ueModuleAccAddress, factoryAddress, &universalAccountId)
		if err != nil {
			execErr = fmt.Errorf("factory lookup failed: %w", err)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
			ueaAddr = ueaAddrRes

			if !isDeployed {
				k.Logger().Info("UEA not deployed, deploying now",
					"utx_key", universalTxKey,
					"source_chain", utx.InboundTx.SourceChain,
					"sender", utx.InboundTx.Sender,
				)
				deployReceipt, dErr := k.DeployUEAV2(ctx, ueModuleAccAddress, &universalAccountId)
				if dErr != nil {
					execErr = fmt.Errorf("DeployUEAV2 failed: %w", dErr)
					shouldRevert = true
					revertReason = execErr.Error()
				} else {
					ueaAddr = common.BytesToAddress(deployReceipt.Ret)

					deployPcTx := types.PCTx{
						TxHash:      deployReceipt.Hash,
						Sender:      ueModuleAddressStr,
						BlockHeight: uint64(sdkCtx.BlockHeight()),
						GasUsed:     deployReceipt.GasUsed,
						Status:      "SUCCESS",
					}
					if updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
						utx.PcTx = append(utx.PcTx, &deployPcTx)
						return nil
					}); updateErr != nil {
						return updateErr
					}
				}
			}

			if execErr == nil && inboundAmount.Sign() > 0 {
				receipt, err = k.depositPRC20(
					sdkCtx,
					utx.InboundTx.SourceChain,
					utx.InboundTx.AssetAddr,
					ueaAddr,
					utx.InboundTx.Amount,
				)
				if err != nil {
					execErr = fmt.Errorf("depositPRC20 failed: %w", err)
					shouldRevert = true
					revertReason = execErr.Error()
				}
			}
		}
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
