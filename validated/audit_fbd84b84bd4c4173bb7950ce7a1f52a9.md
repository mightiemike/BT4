### Title
Unprivileged rescue-outbound griefing permanently locks a victim's stuck-funds recovery path - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
The Olympus governance bug lets a low-power actor occupy a single "active" slot (one active proposal) and block all legitimate uses of that slot for a grace period, repeatable indefinitely. Push Chain's rescue-funds flow in `AttachRescueOutboundFromReceipt` has the same "single active slot per resource" structure: at most one `RESCUE_FUNDS` outbound may be `PENDING`/`OBSERVED` per `UniversalTx` (UTX), and the guard rejects any further rescue attempt while that slot is occupied — with no way for the legitimate owner to force it out except waiting for validator observation to resolve it.

### Finding Description
`AttachRescueOutboundFromReceipt` enforces a per-UTX singleton lock on rescue outbounds: [1](#0-0) 

This function is invoked whenever a `RescueFundsOnSourceChain` event is emitted by the `UniversalGatewayPC` contract and decoded from a Push Chain tx receipt: [2](#0-1) 

Rescue eligibility is gated only on the *target UTX's* state (CEA deposit `FAILED`, or a `REVERTED` auto-`INBOUND_REVERT`) — not on who calls the gateway's rescue function or what `sender`/`gasFee`/`gasLimit`/`prc20` values they pass: [3](#0-2) 

Because the eligibility check is purely UTX-state based, any account able to call the on-chain rescue entrypoint on `UniversalGatewayPC` for a *given* `universalTxId` can trigger this path — including with minimal/dust gas parameters — the moment a victim's UTX becomes rescue-eligible (CEA deposit failed, or inbound-revert reverted). The resulting `RESCUE_FUNDS` outbound is created with `Status_PENDING` and inserted into `PendingOutbounds`: [4](#0-3) 

Once this attacker-triggered rescue outbound sits in `PENDING`/`OBSERVED`, the duplicate-guard above unconditionally rejects **any** subsequent rescue call for that UTX — including the legitimate one from the actual owner/relayer with correct parameters — until the attacker's rescue is externally observed by Universal Validators and finalized to `REVERTED` (or successfully completes, which the attacker can sabotage by using an unreachable/incorrect recipient, wrong gas limit, or a chain where outbound execution predictably fails). This is structurally identical to the Olympus pattern: a single "active slot" (`active rescue == PENDING/OBSERVED`) that any unprivileged caller can occupy with a low-cost, low-quality "dummy" transaction, freezing the legitimate path until validators clear it — and the attacker can repeat this cycle every time the outbound resolves, since there is no additional cost/authorization check tying "who may retry the rescue" to "who owns the recovered funds."

### Impact Explanation
The victim's stuck funds (already failed to deposit on Push Chain, or already failed to auto-revert to the source chain) cannot be rescued while the attacker's dummy rescue occupies the slot. This is a **temporary freezing of user funds** reachable by any unprivileged account that can call the gateway's rescue entrypoint, matching the "permanent/prolonged freezing of user-controlled funds" impact class in scope. Because the attack is cheap to repeat on each resolution cycle (each attempt only needs enough gas to make one EVM call; the attacker never provides real recovery funds and doesn't need any special privilege), a persistent attacker can keep a specific victim's funds frozen indefinitely — a much stronger analog to "held hostage for a grace period" than the original finding, since here the resource can be re-occupied every cycle.

### Likelihood Explanation
Likelihood depends on one unverified fact I could not confirm from the indexed code: whether the `rescueFunds`-style function on `UniversalGatewayPC` is permissionless (callable by anyone, keyed only by `universalTxId`) or restricted to `msg.sender == originalUtx.InboundTx.Sender`/owner. The Go-side keeper code (`AttachRescueOutboundFromReceipt`) performs **no signer/authorization check** — it decodes an on-chain log and trusts it — so if the Solidity contract itself does not restrict the caller, this is fully unprivileged and trivially triggerable by front-running or racing the victim's own rescue call. The Solidity source for `UniversalGatewayPC` was not present in this repository's index (contracts live in a separate `push-chain-core-contracts` repo referenced elsewhere in the README), so I cannot fully verify whether an authorization check exists at the contract layer. If the contract does restrict the caller to the original UTX's sender, this finding does not apply as a cross-account griefing vector, though it would remain true that the *sender itself* can repeatedly relock their own UTX (lower severity, self-DoS only).

### Recommendation
- At minimum, add an explicit authorization check in `AttachRescueOutboundFromReceipt` (or in the Solidity `rescueFunds` function it responds to) requiring the caller/`event.Sender` to match `originalUtx.InboundTx.Sender` or another designated owner/relayer before permitting a new `RESCUE_FUNDS` outbound to be created for that UTX.
- Consider allowing the legitimate owner to force-cancel or supersede a pending rescue outbound that was not initiated by them, or apply a signing-deadline based auto-expiry (similar to `SigningDeadline` already used for standard outbounds) so a stuck attacker-initiated rescue cannot indefinitely block retries.
- If the contract-side check already exists, this should be treated as informational and the guard should still validate `event.Sender` against `originalUtx.InboundTx.Sender` server-side as defense-in-depth, since the chain module currently relies entirely on trusting the decoded log without any independent authorization check.

### Proof of Concept
1. Attacker or an unrelated third party observes (via public chain data) a victim's UTX that has just become rescue-eligible (CEA deposit `FAILED`, or `INBOUND_REVERT` outbound reached `REVERTED`) — visible via `GetUniversalTx`/UTX query endpoints.
2. Attacker calls the `UniversalGatewayPC` rescue entrypoint on Push Chain, referencing the victim's `universalTxId`, using dust/incorrect `gasFee`/`gasLimit`/wrong recipient parameters (attacker does not need to supply real recovery capital; the outbound's `Recipient`/`Amount` are actually derived from the *original* UTX's `InboundTx.Sender`/`Amount`, not from attacker input, so the attacker's cost is only the gas of the call).
3. `AttachRescueOutboundFromReceipt` decodes the `RescueFundsOnSourceChain` log, finds `originalUtx` rescue-eligible, and — since no other rescue is active yet — creates a `RESCUE_FUNDS` `OutboundTx` with `Status_PENDING` and inserts it into `PendingOutbounds` (`x/uexecutor/keeper/create_outbound.go:303-333`).
4. The legitimate owner (or their relayer) subsequently calls the same rescue entrypoint with correct parameters; `AttachRescueOutboundFromReceipt` hits the duplicate guard at lines 269-282 and rejects with `"rescue: UTX %s already has an active rescue outbound"`.
5. The victim's funds remain locked until Universal Validators observe/finalize the attacker's rescue outbound (which the attacker can engineer to fail/route incorrectly, and which the attacker can immediately re-trigger once resolved), repeating the freeze.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L187-237)
```go
// AttachRescueOutboundFromReceipt scans the receipt for RescueFundsOnSourceChain events
// emitted by UniversalGatewayPC and, for each one found, attaches a RESCUE_FUNDS outbound
// to the original UTX referenced by the event's universalTxId.
//
// Unlike normal outbounds (which create a new UTX), rescue outbounds are appended to the
// already-existing UTX whose funds are stuck on the source chain.
func (k Keeper) AttachRescueOutboundFromReceipt(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	evmChainID, err := utils.ExtractEvmChainID(ctx.ChainID())
	if err != nil {
		return fmt.Errorf("rescue: failed to extract EVM chain ID: %w", err)
	}
	pushChainCaip := fmt.Sprintf("eip155:%s", evmChainID)

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
		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.RescueFundsOnSourceChainEventSig) {
			continue
		}

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
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-262)
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
