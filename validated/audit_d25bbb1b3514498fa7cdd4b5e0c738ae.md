Based on my investigation, I found a concrete analog: `x/uexecutor/keeper/create_outbound.go`'s `BuildOutboundsFromReceipt` iterates unbounded over `receipt.Logs` and attaches every matching `UniversalTxOutboundEvent` as a full `OutboundTx` onto the same `UniversalTx`, with no cap. This is the closest structural analog to the "unbounded `tokenIds`/collectors per listing" bug class — a single unprivileged `MsgExecutePayload` can fan out into an unbounded number of outbounds on one `UniversalTx`, each requiring a full DKLS TSS signing ceremony among Universal Validators.

### Title
Unbounded outbound fan-out per UniversalTx from a single payload execution enables validator-side TSS resource exhaustion - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`BuildOutboundsFromReceipt` and `attachOutboundsToUtx` place no limit on how many `UniversalTxOutboundEvent` logs a single EVM receipt can contain, nor on how many `OutboundTx` entries can be appended to one `UniversalTx`. An unprivileged user can submit a `MsgExecutePayload` (or trigger payload execution via an inbound) that calls a contract emitting many such events in one transaction, each of which becomes a `PendingOutboundEntry` requiring an independent TSS signing session by every Universal Validator.

### Finding Description
`k.BuildOutboundsFromReceipt` [1](#0-0)  loops over every log in the EVM receipt with no bound on iteration count, and for every matching `UniversalTxOutboundEventSig` log it performs a `GetTokenConfigByPRC20` / `IsChainOutboundEnabled` lookup and appends a new `OutboundTx`. `attachOutboundsToUtx` then appends each of these to the same `UniversalTx.OutboundTx` slice and creates one `PendingOutbounds` index entry per outbound, unconditionally [2](#0-1) . This is invoked from ordinary, unprivileged payload-execution paths (`ExecuteInboundFundsAndPayload`/`ExecuteInboundGasAndPayload` calling `AttachOutboundsToExistingUniversalTx`) [3](#0-2) , and also from `EVMHooks.PostTxProcessing` on every EVM tx via `CreateUniversalTxFromReceiptIfOutbound` [4](#0-3) . `MsgExecutePayload` is callable by "any" unprivileged user and is on the gasless whitelist [5](#0-4) . Nothing in the module (params, keeper, or genesis) enforces a maximum count of `OutboundTx` per `UniversalTx`, unlike the reported analog where `Listings._createListings()` lacked a cap on `tokenIds`/collectors until a 100-item limit was retrofitted.

Each resulting `PendingOutboundEntry` is picked up independently by the Universal Client's TSS coordinator, which creates a full DKLS signing session and broadcasts setup messages to every participant for each one [6](#0-5) . There is no batching or per-UTX/per-block cap on how many such signing sessions a single malicious payload can enqueue.

### Impact Explanation
A single unprivileged user, via one gasless `MsgExecutePayload`, can force the creation of an arbitrarily large (bounded only by the EVM block gas limit) number of `OutboundTx`/`PendingOutboundEntry` records on one `UniversalTx`. This:
- Permanently bloats the on-chain `UniversalTx` object (append-only, never pruned) and the `PendingOutbounds` index.
- Forces every Universal Validator to run a DKLS signing ceremony per outbound, multiplying off-chain TSS coordination load per single user-submitted transaction — a resource-exhaustion vector against the Universal Validator set reachable without any privileged access, honest nodes/validators, and default transaction submission.

This does not directly steal funds, but is a denial-of-service class impact against TSS coordination reachable from an ordinary user payload.

### Likelihood Explanation
Reaching this requires only deploying/calling an arbitrary contract from a `MsgExecutePayload` UEA/CEA execution that calls `UniversalGatewayPC` to emit many outbound events in one transaction — well within normal, unprivileged usage of the protocol's core payload-execution feature; no governance or admin action is required.

### Recommendation
Introduce a configurable maximum number of `OutboundTx` entries that may be attached to a single `UniversalTx` in one execution (analogous to the 100-item cap the report cites), enforced in `BuildOutboundsFromReceipt`/`attachOutboundsToUtx`, and reject/truncate/require batching beyond that bound rather than admitting unbounded fan-out into TSS signing.

### Proof of Concept
1. Deploy a contract that, in a single call, invokes `UniversalGatewayPC`'s outbound-emitting function N times (N bounded only by EVM block gas limit, e.g. hundreds of iterations well within a high gas-limit chain).
2. Submit a `MsgExecutePayload` (gasless, callable by any address) whose payload targets this contract through the user's UEA.
3. `EVMHooks.PostTxProcessing` / `ExecuteInboundGasAndPayload` calls `BuildOutboundsFromReceipt`, which iterates all N logs and builds N `OutboundTx` entries with no cap.
4. `attachOutboundsToUtx` appends all N entries to one `UniversalTx` and creates N `PendingOutbounds` entries.
5. The Universal Client's TSS coordinator processes each pending outbound as an independent signing event, driving N DKLS signing sessions across all Universal Validators from this single user transaction.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-42)
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L317-333)
```go
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully (gas+payload)",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		payloadPcTx.Status = "SUCCESS"

		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
```

**File:** x/uexecutor/keeper/evm_hooks.go (L25-67)
```go
// PostTxProcessing is called by the EVM module after transaction execution.
// It inspects the receipt and creates UniversalTx + Outbound only if
// UniversalTxWithdraw event is detected.
func (h EVMHooks) PostTxProcessing(
	ctx sdk.Context,
	sender common.Address,
	msg core.Message,
	receipt *ethtypes.Receipt,
) error {
	if receipt == nil || len(receipt.Logs) == 0 {
		return nil
	}

	h.k.Logger().Debug("evm hook post-tx processing",
		"tx_hash", receipt.TxHash.Hex(),
		"sender", sender.Hex(),
		"log_count", len(receipt.Logs),
		"gas_used", receipt.GasUsed,
	)

	protoReceipt := &evmtypes.MsgEthereumTxResponse{
		Hash:    receipt.TxHash.Hex(),
		GasUsed: receipt.GasUsed,
		Logs:    convertReceiptLogs(receipt.Logs),
	}

	// Build pcTx representation
	pcTx := types.PCTx{
		Sender:      sender.Hex(),
		TxHash:      protoReceipt.Hash,
		GasUsed:     protoReceipt.GasUsed,
		BlockHeight: uint64(ctx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
}
```

**File:** x/uexecutor/README.md (L199-205)
```markdown
| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |
```

**File:** universalClient/tss/coordinator/coordinator.go (L479-551)
```go
// processEventAsCoordinator processes a TSS event as the coordinator.
// Creates setup message based on event type and sends to all participants.
// assignedNonce is set only for SIGN events; nil for keygen/keyrefresh/quorumchange.
func (c *Coordinator) processEventAsCoordinator(ctx context.Context, event store.Event, participants []*types.UniversalValidator, assignedNonce *uint64) error {
	// Sort participants by party ID for consistency
	sortedParticipants := make([]*types.UniversalValidator, len(participants))
	copy(sortedParticipants, participants)
	sort.Slice(sortedParticipants, func(i, j int) bool {
		addrI := ""
		addrJ := ""
		if sortedParticipants[i].IdentifyInfo != nil {
			addrI = sortedParticipants[i].IdentifyInfo.CoreValidatorAddress
		}
		if sortedParticipants[j].IdentifyInfo != nil {
			addrJ = sortedParticipants[j].IdentifyInfo.CoreValidatorAddress
		}
		return addrI < addrJ
	})

	// Extract party IDs
	partyIDs := make([]string, len(sortedParticipants))
	for i, p := range sortedParticipants {
		if p.IdentifyInfo != nil {
			partyIDs[i] = p.IdentifyInfo.CoreValidatorAddress
		}
	}

	// Calculate threshold
	threshold := CalculateThreshold(len(partyIDs))

	// Create setup message based on event type
	var setupData []byte
	var unsignedTxReq *common.UnsignedSigningReq
	var err error
	switch event.Type {
	case store.EventTypeKeygen, store.EventTypeKeyrefresh:
		// Keygen and keyrefresh use the same setup structure
		setupData, err = c.createKeygenSetup(threshold, partyIDs)
	case store.EventTypeQuorumChange:
		setupData, err = c.createQcSetup(ctx, threshold, partyIDs, sortedParticipants)
	case store.EventTypeSignOutbound:
		setupData, unsignedTxReq, err = c.createSignSetup(ctx, event.EventData, partyIDs, assignedNonce)
	case store.EventTypeSignFundMigrate:
		setupData, unsignedTxReq, err = c.createFundMigrationSignSetup(ctx, event.EventData, partyIDs, assignedNonce)
	default:
		err = fmt.Errorf("unknown protocol type: %s", event.Type)
	}

	if err != nil {
		return fmt.Errorf("failed to create setup message for event %s: %w", event.EventID, err)
	}

	// Create and send setup message to all participants
	setupMsg := Message{
		Type:               MessageTypeSetup,
		EventID:            event.EventID,
		Payload:            setupData,
		Participants:       partyIDs,
		UnsignedSigningReq: unsignedTxReq, // nil for non-sign events
	}
	setupMsgBytes, err := json.Marshal(setupMsg)
	if err != nil {
		return fmt.Errorf("failed to marshal setup message for event %s: %w", event.EventID, err)
	}

	// Initialize ACK tracking for this event
	c.ackMu.Lock()
	c.ackTracking[event.EventID] = &ackState{
		participants: partyIDs,
		ackedBy:      make(map[string]bool),
		ackCount:     0,
	}
	c.ackMu.Unlock()
```
