### Title
`AttachRescueOutboundFromReceipt` trusts attacker-controlled `PRC20` and `UniversalTxId` from the emitted log without binding them to the original inbound’s actual token or depositor - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`Keeper.AttachRescueOutboundFromReceipt` decodes a `RescueFundsOnSourceChain` log and re-attaches a `RESCUE_FUNDS` outbound to an *existing* UTX identified purely by `event.UniversalTxId`, a value copied verbatim from the emitted log topic [1](#0-0) . The only checks performed before attaching the outbound are (a) the target UTX exists, (b) it is in an eligible "stuck funds" state (CEA deposit `FAILED`, or a `REVERTED` `INBOUND_REVERT`), and (c) no other active rescue outbound already exists [2](#0-1) . Nothing in this function verifies that the caller who triggered the on-chain rescue call is the original depositor of that UTX, nor that `event.PRC20` corresponds to the token actually locked for that UTX's inbound.

### Finding Description
Once the target UTX is picked purely from the attacker-influenced `event.UniversalTxId`, the resolved external asset address is derived from **attacker-supplied** `event.PRC20` looked up against the *original UTX's source chain*, not against the token that was actually part of that UTX's inbound deposit: [3](#0-2) 

The resulting outbound then combines:
- `Amount` — taken correctly from the original UTX's inbound amount (not attacker-controlled) [4](#0-3) 
- `ExternalAssetAddr` — resolved from the attacker-chosen `event.PRC20`, i.e. potentially a token unrelated to the funds actually stuck on the source chain for that UTX
- `Recipient` — fixed to `originalUtx.InboundTx.Sender` or its stored `RevertInstructions.FundRecipient` [5](#0-4) 

The recipient-binding is correctly hardened (funds cannot be redirected to the attacker), which rules out direct theft. However, the asset/token binding is not similarly hardened: because `ExternalAssetAddr` is derived from attacker-controlled `event.PRC20` rather than the PRC20 actually recorded on `originalUtx.InboundTx`, a caller can attach a rescue outbound that pairs the correct locked `Amount` with the wrong `ExternalAssetAddr`/`Prc20AssetAddr`. Once validators vote and finalize this outbound via `MsgVoteOutbound`, TSS/relayer infrastructure would attempt to release the wrong asset on the destination chain against the original UTX's stuck-fund entry — this can misroute value (releasing an unrelated token) or cause the outbound to permanently fail/observe an inconsistent state, corrupting the UTX's outbound history and the PRC20/native accounting invariant that rescue outbounds must stay bound to the *same* asset that was actually locked for that specific UTX.

Additionally, there is no check that the account triggering the on-chain `RescueFundsOnSourceChain` event is the original depositor (`originalUtx.InboundTx.Sender`) or an authorized party for that UTX — any address can trigger this call once the target UTX becomes eligible (deposit `FAILED` or auto-revert `REVERTED`), as long as it supplies a `PRC20` value that resolves via `GetTokenConfigByPRC20`.

**Unverified dependency:** the Solidity source of the `UniversalGatewayPC.rescueFundsOnSourceChain` (or equivalent) function that emits this event is not present in this Go repository (only compiled bytecode references exist, e.g., `test/utils/bytecode.go`), so it could not be confirmed whether the EVM contract itself restricts the caller to the original depositor or validates `PRC20` against the UTX's recorded token before emitting the event. If the contract enforces those bindings, this keeper-side gap is not independently exploitable; if it does not, the keeper is the only remaining line of defense and it does not perform this validation.

### Impact Explanation
If the underlying Solidity contract does not restrict who may call the rescue function or which `PRC20`/`universalTxId` pair is valid, an unprivileged attacker could attach a rescue outbound to any other user's eligible stuck-fund UTX with an unrelated `PRC20`/`ExternalAssetAddr`. Because the recipient stays anchored to the legitimate original sender, this is not direct theft to the attacker, but it can misroute the asset used for the on-chain release, corrupting token/accounting invariants and potentially causing the rescue attempt to permanently fail (freezing the depositor's actual stuck funds indefinitely, since the duplicate-active-rescue guard blocks a corrected retry while the malformed one is `PENDING`/`OBSERVED`) [6](#0-5) .

### Likelihood Explanation
Likelihood is contingent on unverifiable contract-level behavior. If the Solidity `UniversalGatewayPC` contract already enforces caller-authorization and token-binding for its rescue function (a reasonable design expectation), this path is not exploitable and the keeper's lack of redundant validation is a defense-in-depth gap rather than a live vulnerability. Given the audit scope excludes assuming malicious relayers/validators but does include unprivileged users interacting with in-scope contract entrypoints, this should be treated as a likely-but-unconfirmed issue pending contract source review.

### Recommendation
- In `AttachRescueOutboundFromReceipt`, validate `event.PRC20` against the token actually recorded on `originalUtx.InboundTx` (e.g., its `Token`/`Prc20AssetAddr` field) rather than trusting the event's field independently, and reject mismatches.
- Consider validating `event.Sender` against `originalUtx.InboundTx.Sender` (or an explicitly authorized rescuer) to prevent third parties from triggering rescue flows on UTXs they do not own, even if funds cannot be redirected.
- Confirm and, if necessary, harden the Solidity `rescueFundsOnSourceChain` function so it independently binds `universalTxId` to `msg.sender` and to the locked token address, so the keeper is not the sole point of validation.

### Proof of Concept
1. Identify any UTX belonging to another user that is currently eligible for rescue (CEA deposit `FAILED`, or non-CEA with `REVERTED` `INBOUND_REVERT`) — this state is externally observable.
2. Trigger (or, if the contract permits any caller, directly call) the gateway's rescue function supplying that victim's `universalTxId` together with a `PRC20` address for a different, unrelated token that still resolves via `GetTokenConfigByPRC20` for the victim UTX's source chain.
3. `AttachRescueOutboundFromReceipt` processes the resulting log: it looks up the victim UTX purely by `event.UniversalTxId` [7](#0-6) , passes eligibility checks unrelated to the caller or token match [8](#0-7) , and attaches an outbound whose `Amount` (victim's real locked funds) is paired with the attacker-chosen `ExternalAssetAddr`/`Prc20AssetAddr` [9](#0-8) .
4. Inspect the resulting `UniversalTx.OutboundTx` entry: the destination chain, amount, and mismatched external asset address confirm the token/asset binding was corrupted for that UTX, while the duplicate-active-rescue guard now blocks the legitimate depositor from submitting a corrected rescue until this malformed one resolves.

*Note: step 2's actual feasibility depends on the (unverified) Solidity contract's access controls, which could not be located in this repository.*

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L132-136)
```go
	event := &RescueFundsOnSourceChainEvent{
		UniversalTxId: log.Topics[1], // bytes32 as 0x-prefixed hex
		PRC20:         common.HexToAddress(log.Topics[2]).Hex(),
		Sender:        common.HexToAddress(log.Topics[3]).Hex(),
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L225-234)
```go
		// The universalTxId in the event is a 0x-prefixed bytes32 matching our UTX key.
		originalUtxId := strings.TrimPrefix(event.UniversalTxId, "0x")

		originalUtx, found, err := k.GetUniversalTx(ctx, originalUtxId)
		if err != nil {
			return fmt.Errorf("rescue: failed to fetch UTX %s: %w", originalUtxId, err)
		}
		if !found {
			return fmt.Errorf("rescue: original UTX %s not found", originalUtxId)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L235-282)
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

**File:** x/uexecutor/keeper/create_outbound.go (L284-293)
```go
		// Resolve external asset address from PRC20 → token config for the source chain.
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			originalUtx.InboundTx.SourceChain,
			event.PRC20,
		)
		if err != nil {
			return fmt.Errorf("rescue: token config not found for PRC20 %s on %s: %w",
				event.PRC20, originalUtx.InboundTx.SourceChain, err)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L295-300)
```go
		// Rescued funds go to the original revert recipient (or the sender as fallback).
		recipient := originalUtx.InboundTx.Sender
		if originalUtx.InboundTx.RevertInstructions != nil &&
			originalUtx.InboundTx.RevertInstructions.FundRecipient != "" {
			recipient = originalUtx.InboundTx.RevertInstructions.FundRecipient
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L303-320)
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
```
