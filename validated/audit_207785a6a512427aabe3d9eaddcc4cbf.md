I found a real analog of the reported bug class here — the `isCEA` inbound path explicitly opts out of the revert/refund mechanism that every other execution-failure path in `x/uexecutor` uses.

### Title
Failed `isCEA` inbound deposits are permanently unrecoverable — no revert outbound is created, mirroring the VETH "invalidated proof, no retry" bug class - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
In the Vader report, a merkle-proof-gated conversion invalidates the user's proof of eligibility on any incidental failure (zero allowance), permanently stranding the user with a deprecated asset and no path to retry or recover value. Push Chain's inbound-execution pipeline has the same shape: an inbound is admitted into consensus once (ballot passes, `UniversalTx` created, `PendingInbounds` entry removed — this is the one-shot "proof"), and subsequent execution failure is supposed to trigger a compensating `INBOUND_REVERT` outbound so the user's locked source-chain funds come back. But for `isCEA` inbounds specifically, that compensating path is explicitly disabled.

### Finding Description
`VoteInbound` (`x/uexecutor/keeper/msg_vote_inbound.go`) finalizes the ballot and unconditionally creates the `UniversalTx`, then calls `ExecuteInbound` → `ExecuteInboundFundsAndPayload`. Once the UTX is created and `PendingInbounds` cleaned up, the inbound's `utx_key` can never be resubmitted — `VoteInbound` rejects any inbound whose UTX key already exists: [1](#0-0) 

In `ExecuteInboundFundsAndPayload`, for the `isCEA` branch, if `depositPRC20` fails (e.g. `depositPRC20 failed: ...`, analogous to the "zero allowance" condition in Converter.sol causing a downstream revert), the code explicitly documents and enforces that **no revert outbound is ever attached**: [2](#0-1) 

Compare this with the non-`isCEA` branch and `ExecuteInboundGas`, both of which set `shouldRevert = true` on the same class of failure (`depositPRC20 failed`) and build a compensating outbound: [3](#0-2) [4](#0-3) 

The failure path for `isCEA` only records a `FAILED` `PCTx` entry and returns `nil`: [5](#0-4) 

The module's own README documents this asymmetry as intentional design ("isCEA failures never create an INBOUND_REVERT outbound"), and separately documents that the "escape-hatch refund flow" for exhausted/expired inbounds is a **future**, not-yet-implemented feature: [6](#0-5) 

So the user's source-chain funds — already locked/bridged into the gateway to trigger this inbound — have no on-chain path back once `depositPRC20` fails for an `isCEA` inbound. This is functionally identical to the Vader bug: a one-time, user-triggered event (merkle proof / inbound observation+ballot) is consumed permanently, and an incidental execution failure (zero allowance / failed PRC20 deposit) leaves the user's asset stuck with no retry and no refund.

### Impact Explanation
This falls within the allowed impact gate: "permanent loss, permanent freezing... of user or protocol-controlled funds" reachable via "ordinary user deposits... alone," with no privileged actor required. Any ordinary user submitting an `isCEA` inbound (a normal cross-chain deposit specifying an explicit non-UEA/EOA-adjacent recipient) whose `depositPRC20` call fails for any transient or configuration reason (e.g., `GetTokenConfig`/`GetNativeRepresentation` misconfiguration, insufficient PRC20 mint capacity, EVM-level revert in the PRC20 contract) permanently loses access to their bridged funds — they are recorded as `FAILED` in `PcTx` with no compensating outbound and no way to resubmit the same inbound (blocked by the `HasUniversalTx` existence check). This matches the Vader report's core harm: value effectively becomes permanently stuck due to a design gap, not because of malicious action.

### Likelihood Explanation
This requires only an honest inbound (any depositor triggering an `isCEA` inbound) and an ordinary execution-time failure of `depositPRC20` — no adversarial validator or privileged behavior needed. Any transient failure mode in the deposit path (e.g. PRC20 mint cap, incorrect/paused token config, `CallFactoryGetOriginForUEA` mismatch categorized as failure) will trigger this uncompensated dead-end, and the code path is explicitly and permanently disabled by design (not a bug introduced accidentally, but the documented behavior), so it will reproduce deterministically whenever the underlying deposit call fails for `isCEA` inbounds.

### Recommendation
Do not treat `isCEA` deposit failures differently from the general funds path. When `depositPRC20` (or the UEA-check step) fails for an `isCEA` inbound, schedule an `INBOUND_REVERT` outbound exactly as `ExecuteInboundFundsAndPayload`'s non-`isCEA` branch and `ExecuteInboundGas` already do, so the user's source-chain funds are returned. If there is a specific reason `isCEA` inbounds cannot be safely reverted (e.g., ambiguity about destination-chain refund address), implement the "escape-hatch refund flow" referenced in the README (`ExpiredInbounds`-style resolution) before shipping this asymmetry, rather than leaving affected funds permanently stuck with only a `FAILED` `PcTx` audit trail.

### Proof of Concept
1. A user bridges an asset from a source chain with `isCEA=true` and `Recipient` set to a valid hex address that is neither a UEA nor a UEA-adjacent smart contract (or is, but the PRC20 deposit will fail — e.g., token config for that `AssetAddr`/`SourceChain` is later paused or misconfigured, or the PRC20 mint reverts).
2. Universal Validators observe and vote the inbound; ballot passes; `VoteInbound` creates the `UniversalTx` and removes the `PendingInbounds` entry (one-shot admission, analogous to the merkle proof being marked used).
3. `ExecuteInboundFundsAndPayload` reaches the `isCEA` branch, calls `depositPRC20`, which fails.
4. Per lines 53–103, no revert outbound is built; only a `FAILED` `PCTx` is appended to the UTX (lines 161–206).
5. The user's already-locked source-chain funds have no path back: resubmitting the same inbound is blocked by the `HasUniversalTx` check in `VoteInbound` (lines 45–52), and there is no other message or automated flow that revisits a `FAILED` `isCEA` UTX to retry the deposit or refund the source-chain sender.

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L45-52)
```go
	found, err := k.HasUniversalTx(ctx, universalTxKey)
	if err != nil {
		return errors.Wrap(err, "failed to check UniversalTx")
	}
	if found {
		k.Logger().Warn("vote inbound rejected: utx already exists", "utx_key", universalTxKey)
		return fmt.Errorf("universal tx with key %s already exists", universalTxKey)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L144-157)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L161-206)
```go
	// --- record deposit attempt (only if amount > 0 or there was an error)
	if inboundAmount.Sign() > 0 || execErr != nil {
		depositPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			depositPcTx.TxHash = receipt.Hash
			depositPcTx.GasUsed = receipt.GasUsed
		}
		if execErr != nil {
			depositPcTx.ErrorMsg = execErr.Error()
		} else {
			depositPcTx.Status = "SUCCESS"
		}
		updateErr := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &depositPcTx)
			return nil
		})
		if updateErr != nil {
			return updateErr
		}
	}

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

**File:** x/uexecutor/README.md (L254-258)
```markdown
- **Removed** when ALL related ballot variants reach a terminal state. If any
  variant ended `PASSED`, the existing post-finalization path in `VoteInbound`
  produced a `UniversalTx`. If ALL variants ended `EXPIRED`/`REJECTED`, the
  full per-variant audit trail is moved to `ExpiredInbounds` for the future
  escape-hatch refund flow.
```
