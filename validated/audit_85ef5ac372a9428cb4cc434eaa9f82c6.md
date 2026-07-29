## Title
Revert-outbound creation paths never check `IsChainOutboundEnabled`, permanently stranding user funds when a chain is inbound-enabled but outbound-disabled - (File: `x/uexecutor/keeper/build_revert_outbound.go`)

### Summary
Push Chain's forward-outbound creation path (`BuildOutboundsFromReceipt`) explicitly verifies `IsChainOutboundEnabled` before attaching an outbound, and refuses to create one for a chain where outbound is disabled. However, every **revert-outbound** creation path — used when an inbound's PRC20 deposit or payload execution fails — builds and attaches a `PENDING` `INBOUND_REVERT` outbound targeting `inbound.SourceChain` without ever checking whether outbound is enabled for that chain. This is the same "optimistic peer/destination assumption" defect described in the external report: the code assumes the destination is always reachable and only discovers otherwise deep in the TSS signing pipeline, where the request is silently and permanently rejected, leaving the outbound stuck in `PENDING` forever.

### Finding Description
`buildRevertOutbound` constructs an `OutboundTx` whose `DestinationChain` is simply `inbound.SourceChain`, with no registry lookup for whether outbound is enabled on that chain: [1](#0-0) 

This helper is invoked from every failure path that needs to refund a user on the source chain:
- `ExecuteInboundFunds` on deposit failure: [2](#0-1) 
- `ExecuteInboundFundsAndPayload` on deposit/factory/deploy failure: [3](#0-2) 
- `ExecuteInboundGasAndPayload`: [4](#0-3) 
- `handleFailedInboundValidation` (post-ballot validation failure): [5](#0-4) 
- `RevertStuckInbound` (admin escape hatch, but still just calls the same unchecked builder): [6](#0-5) 

None of these call sites, nor `attachOutboundsToUtx` itself, invoke `k.uregistryKeeper.IsChainOutboundEnabled`: [7](#0-6) 

This is inconsistent with the **forward**-outbound path, which does perform this exact check and rejects the outbound outright if disabled: [8](#0-7) 

The consequence surfaces only later, in the TSS signing pipeline: `verifyOutboundSigningRequest` in the Universal Client explicitly refuses to sign any outbound whose destination chain has outbound disabled: [9](#0-8) 

Because the on-chain `OutboundTx` was already created in `PENDING` status and indexed in `PendingOutbounds`, and TSS unconditionally refuses to ever sign it, the outbound has no path forward: it cannot be broadcast, and there is no on-chain mechanism to retry, cancel, or redirect it once created (this is by design — see `x/uregistry`'s comment that inbound/outbound flags are independently admin-controlled, `IsInboundEnabled`/`IsOutboundEnabled`): [10](#0-9) 

### Impact Explanation
When a chain is configured with `IsInboundEnabled = true` and `IsOutboundEnabled = false` (a legitimate, independently-toggleable admin state, e.g. during a phased chain rollout or TSS key migration where outbound signing is intentionally paused), any ordinary user inbound to that chain that triggers a deposit/payload execution failure (malformed recipient, missing token config, factory/UEA-deploy failure, swap failure, etc.) causes the keeper to create a `PENDING` `INBOUND_REVERT` outbound targeting that same chain. Since the user's original funds are locked in the source-chain gateway/vault and were never minted on Push Chain (deposit failed before minting), the only way to make the user whole is this revert outbound actually being signed and broadcast. Because TSS permanently refuses to sign outbounds for outbound-disabled chains, and the protocol has no path to reroute or retry a stuck `PendingOutbounds` entry, the user's original bridged funds become **permanently unrecoverable** — matching the "HIGH impact" characterization in the external report.

### Likelihood Explanation
Reaching this state requires no privileged action from the attacker's perspective — it only requires (a) a chain in the inbound-enabled/outbound-disabled state (a valid, foreseeable, non-malicious admin configuration explicitly modeled by `ChainEnabled.IsInboundEnabled`/`IsOutboundEnabled` being independent flags) and (b) an ordinary/attacker-crafted inbound that causes execution to fail on Push Chain (e.g., supplying a malformed recipient in an `isCEA`/`FUNDS_AND_PAYLOAD` inbound, or a token whose config was later removed). Likelihood is LOW-to-MEDIUM since it depends on this specific chain-config combination existing, but once it does, ordinary users hitting normal failure conditions are permanently affected — mirroring the report's own "medium severity, high impact, low likelihood" rating.

### Recommendation
Add an `IsChainOutboundEnabled` check (mirroring `BuildOutboundsFromReceipt`) inside `buildRevertOutbound` / `attachOutboundsToUtx` before creating any revert (or admin-revert) outbound. If outbound is disabled for the target chain, do not silently create an unroutable `PENDING` outbound; instead mark the UTX with an explicit `ABORTED`/error state (as is already done elsewhere via `AbortOutbound`) so it is flagged for manual intervention rather than silently stuck forever, and/or block admin from disabling outbound on a chain that still has pending inbound-derived reverts targeting it.

### Proof of Concept
1. Admin registers chain `eip155:X` with `Enabled.IsInboundEnabled = true`, `Enabled.IsOutboundEnabled = false` (a supported independent configuration per `uregistrytypes.ChainEnabled`).
2. A user (or attacker) submits/triggers an inbound of type `FUNDS_AND_PAYLOAD` with `isCEA = true` and a malformed/incompatible recipient (or a payload causing `depositPRC20` to fail).
3. Universal Validators vote the inbound to quorum; `ExecuteInboundFundsAndPayload` runs, `execErr != nil`, `shouldRevert = true`.
4. `buildRevertOutbound` creates an `OutboundTx{DestinationChain: "eip155:X", Status: PENDING}` and `attachOutboundsToUtx` indexes it in `PendingOutbounds` — no check against `IsChainOutboundEnabled` occurs.
5. Universal Validators pick up the pending outbound and attempt to sign it via TSS; `verifyOutboundSigningRequest` returns `"outbound disabled for chain %s, refusing to sign"` for every attempt, forever.
6. The user's original funds (locked in the source-chain vault, never minted on Push Chain because the deposit failed) can never be recovered — the `UniversalTx` sits with a `PENDING` outbound indefinitely.

### Citations

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-25)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L190-209)
```go
	// --- create revert ONLY for pre-deposit / deposit failures (non-isCEA path)
	if execErr != nil && shouldRevert {
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

		return nil
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

**File:** x/uexecutor/keeper/admin_revert.go (L73-80)
```go
	revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)
	if revertOutbound == nil {
		return "", "", fmt.Errorf("failed to build revert outbound for inbound %s", universalTxKey)
	}

	if attachErr := k.attachOutboundsToUtx(sdkCtx, universalTxKey, []*types.OutboundTx{revertOutbound}, "admin revert: stuck ballot expired"); attachErr != nil {
		return "", "", fmt.Errorf("failed to attach revert outbound: %w", attachErr)
	}
```

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

**File:** x/uexecutor/keeper/create_outbound.go (L339-372)
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

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L915-923)
```go
	chainID := outboundData.DestinationChain
	if chainID == "" {
		return fmt.Errorf("destination chain is missing")
	}

	// Reject signing if outbound is disabled for the destination chain
	if sm.chains != nil && !sm.chains.IsChainOutboundEnabled(chainID) {
		return fmt.Errorf("outbound disabled for chain %s, refusing to sign", chainID)
	}
```

**File:** x/uregistry/keeper/keeper.go (L195-225)
```go
// IsChainInboundEnabled checks if inbound is enabled for a given chain
func (k Keeper) IsChainInboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsInboundEnabled, nil
}

// IsChainOutboundEnabled checks if outbound is enabled for a given chain
func (k Keeper) IsChainOutboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsOutboundEnabled, nil
}
```
