Based on my research, I found a concrete analog to the `predict()`/`claimReward()` unbounded-array pattern in Push Chain's `x/uexecutor` module.

### Title
Unbounded outbound-array growth on a single UniversalTx via attacker-controlled payload gas limit - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`BuildOutboundsFromReceipt` iterates every log in an EVM receipt with no cap on the number of `UniversalTxOutbound` events processed, and `attachOutboundsToUtx` appends every one of them into a single `UniversalTx.OutboundTx` slice in one atomic keeper call. [1](#0-0)  An inbound sender fully controls the `UniversalPayload` executed on their own UEA, including an attacker-chosen `GasLimit` that is passed straight into the EVM call with no maximum-cap validation found anywhere in the payload validation path. [2](#0-1) [3](#0-2) 

### Finding Description
The attack surface mirrors the report's core defect: an unprivileged actor can cheaply cause an array tied to shared/critical state to grow without bound, and that state is later iterated in ways whose cost scales with the array size.

1. A user submits an inbound (`FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`) whose `UniversalPayload.Data` calls a malicious contract that loops and calls the Universal Gateway's `withdraw`-style function many times, emitting many `UniversalTxOutbound` events in a single receipt, and sets `UniversalPayload.GasLimit` as high as the node will accept.
2. When honest validators reach quorum on `VoteInbound`, `ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` run `ExecutePayloadV2` → `CallUEAExecutePayload` → `k.evmKeeper.DerivedEVMCall`, using the attacker-supplied `GasLimit` with no visible ceiling check. [4](#0-3) 
3. `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt` then walks every log in the resulting receipt and builds an `OutboundTx` for each matching event, with no limit on count. [5](#0-4) 
4. `attachOutboundsToUtx` appends all of them into `utx.OutboundTx` in one `UpdateUniversalTx` closure, also writing a `PendingOutbounds` index entry and emitting an event per outbound — all inside a single state-machine transition driven by an honest validator's `MsgVoteInbound`. [6](#0-5) 
5. Downstream, every subsequent `VoteOutbound` call performs a **linear scan** of the entire `utx.OutboundTx` slice to locate the target outbound by ID: `for _, ob := range utx.OutboundTx { if ob.Id == outboundId ... }`. [7](#0-6)  With thousands of outbounds attached to one UTX, this scan (and the `UpdateUniversalTx` re-marshal of the whole growing slice) is repeated by every validator for every one of those outbounds, and the `RescueFundsOnSourceChain`/duplicate-rescue-guard path also linearly scans `originalUtx.OutboundTx` on every rescue attempt. [8](#0-7) 

This is the same shape as the reported bug: a cheap, attacker-controlled action (predict() fee ↔ here, one bridged inbound with a malicious payload) grows an array with no cap, and later per-item processing (claimReward() loop ↔ here, per-outbound voting/marshalling) scales with that size, threatening to make legitimate outbound processing (and thus fund settlement/refund) prohibitively expensive for validators, potentially stalling resolution of that UTX's outbounds indefinitely.

### Impact Explanation
If reachable, this could make processing (voting/finalizing/rescuing) any of the outbounds attached to a bloated `UniversalTx` costly enough to be impractical, effectively freezing the underlying bridged funds tied to those pending outbounds (matching the "permanent freezing of user funds" and "denial of service, non-network-level, unprivileged" impact categories in scope). Because `PendingOutbounds` entries are only removed on validator consensus (never by ballot expiry, per the module's own design docs), a UTX stuck with an unmanageable number of outbound entries has no automatic recovery path. [9](#0-8) 

### Likelihood Explanation
Likelihood is **uncertain/likely low-to-moderate** and I could not fully confirm exploitability within the available index:
- I could not locate an explicit maximum cap on `UniversalPayload.GasLimit` or on the number of `UniversalTxOutbound` events processed per receipt in `BuildOutboundsFromReceipt`; both appear unbounded in the code reviewed.
- However, the number of outbound events an attacker can realistically emit in one EVM call is bounded by the Push Chain block gas limit and EVM `LOG` opcode costs, not purely by the attacker's `GasLimit` field — I was unable to verify what block gas limit or additional gas-metering (e.g., whether `DerivedEVMCall` uses an infinite or bounded gas meter for module-originated calls) applies to this internal call, which materially affects how many outbound entries can practically be produced in a single transaction.
- The severity also depends on how large `utx.OutboundTx` must grow before per-vote linear scans and full-UTX re-marshalling become a practical DoS versus just added overhead — this was not something I could benchmark from static analysis alone.

### Recommendation
- Enforce a maximum number of `UniversalTxOutbound` events processed per receipt in `BuildOutboundsFromReceipt` (and/or a maximum `OutboundTx` count per `UniversalTx`), rejecting or splitting excess events rather than attaching them all unconditionally.
- Cap `UniversalPayload.GasLimit` to a sane maximum during payload validation (`NewAbiUniversalPayload`/`ValidateForExecution`) so a single inbound-triggered EVM call cannot emit an unbounded number of log events.
- Replace the linear `for _, ob := range utx.OutboundTx` lookups in `VoteOutbound` and the rescue-outbound duplicate check with an indexed lookup (e.g., a secondary map keyed by outbound ID) to avoid O(n) cost scaling with attached outbound count.

### Proof of Concept
Not independently verified end-to-end (would require deploying a malicious payload contract and measuring actual EVM gas/log limits against the Push Chain block gas limit, which I could not execute in this read-only analysis). Conceptually:
1. Attacker deploys a contract on a source chain (or via the UEA target) that, when called via `UniversalPayload.Data`, loops N times calling the Universal Gateway's outbound/withdraw function, each iteration emitting one `UniversalTxOutbound` event.
2. Attacker submits a `FUNDS_AND_PAYLOAD` inbound referencing this payload with a maximal `GasLimit`.
3. Once validators vote `VoteInbound` to quorum, `ExecuteInboundFundsAndPayload` executes the payload, and `BuildOutboundsFromReceipt`/`attachOutboundsToUtx` attach all N outbound entries to one `UniversalTx`.
4. Confirm the resulting UTX has N `OutboundTx` entries and measure the cost/time of subsequent `VoteOutbound` calls against that UTX as N grows.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-104)
```go
func (k Keeper) BuildOutboundsFromReceipt(
	ctx context.Context,
	utxId string,
	receipt *evmtypes.MsgEthereumTxResponse,
) ([]*types.OutboundTx, error) {

	outbounds := []*types.OutboundTx{}
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	k.Logger().Debug("building outbounds from receipt", "utx_id", utxId, "tx_hash", receipt.Hash, "log_count", len(receipt.Logs))

	for _, lg := range receipt.Logs {
		if lg.Removed {
			continue
		}

		if strings.ToLower(lg.Address) != universalGatewayPC {
			continue
		}

		if len(lg.Topics) == 0 {
			continue
		}

		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.UniversalTxOutboundEventSig) {
			continue
		}

		event, err := types.DecodeUniversalTxOutboundFromLog(lg)
		if err != nil {
			return nil, fmt.Errorf("failed to decode UniversalTxWithdraw: %w", err)
		}

		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}

		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}

		k.Logger().Debug("outbound built from receipt",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"dest_chain", outbound.DestinationChain,
			"amount", outbound.Amount,
			"tx_type", outbound.TxType.String(),
		)
		outbounds = append(outbounds, outbound)
	}

	k.Logger().Debug("outbounds built from receipt", "utx_id", utxId, "count", len(outbounds))
	return outbounds, nil
```

**File:** x/uexecutor/keeper/create_outbound.go (L252-282)
```go
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

**File:** x/uexecutor/keeper/create_outbound.go (L339-407)
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

			var pcTxHash string
			var logIndex string

			if outbound.PcTx != nil {
				pcTxHash = outbound.PcTx.TxHash
				logIndex = outbound.PcTx.LogIndex
			}

			evt, err := types.NewOutboundCreatedEvent(types.OutboundCreatedEvent{
				UniversalTxId:    utxId,
				TxID:             outbound.Id,
				DestinationChain: outbound.DestinationChain,
				Recipient:        outbound.Recipient,
				Amount:           outbound.Amount,
				AssetAddr:        outbound.ExternalAssetAddr,
				Sender:           outbound.Sender,
				Payload:          outbound.Payload,
				GasFee:           outbound.GasFee,
				GasLimit:         outbound.GasLimit,
				GasPrice:         outbound.GasPrice,
				GasToken:         outbound.GasToken,
				TxType:           outbound.TxType.String(),
				PcTxHash:         pcTxHash,
				LogIndex:         logIndex,
				RevertMsg:        revertMsg,
				SigningDeadline:  signingDeadline,
			})
			if err == nil {
				ctx.EventManager().EmitEvent(evt)
			}
		}

		return nil
	})
}
```

**File:** x/uexecutor/keeper/evm.go (L172-192)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
```

**File:** x/uexecutor/keeper/execute_payload.go (L17-33)
```go
func (k Keeper) ExecutePayloadV2(ctx context.Context, evmFrom common.Address, ueaAddr common.Address, universalPayload *types.UniversalPayload, verificationData string) (*vmtypes.MsgEthereumTxResponse, error) {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Debug("execute payload v2",
		"uea", ueaAddr.Hex(),
		"from", evmFrom.Hex(),
	)

	// Step 1: Validate payload and verificationData early (fast-fail before EVM work)
	if _, err := types.NewAbiUniversalPayload(universalPayload); err != nil {
		return nil, errors.Wrapf(err, "invalid universal payload")
	}

	verificationDataVal, err := utils.HexToBytes(verificationData)
	if err != nil {
		return nil, errors.Wrapf(err, "invalid verificationData format")
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L283-298)
```go
	}

	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)

	payloadPcTx := types.PCTx{
		Sender:      ueModuleAddressStr,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "FAILED",
	}
	// Capture tx hash from receipt even on EVM revert for debugging.
	if receipt != nil {
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L42-54)
```go
	// Step 2: Find outbound by id
	var outbound types.OutboundTx
	found = false
	for _, ob := range utx.OutboundTx {
		if ob.Id == outboundId {
			outbound = *ob
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("outbound %s not found in UniversalTx %s", outboundId, utxId)
	}
```

**File:** x/uexecutor/README.md (L262-282)
```markdown
### `PendingOutbounds`

- **Created** by chain code at outbound creation in `create_outbound.go` —
  BEFORE any validator vote. The chain knows the outbound exists because it
  generated the destination-chain transaction itself; validators are tasked
  with observing whether/how it landed.
- **Keyed** by deterministic chain-derived `outbound_id`.
- **Variant-aware:** validator votes append `OutboundObservationVariant`s as
  they arrive (`RecordOutboundVote` inside `VoteOutbound`). Multiple variants
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
