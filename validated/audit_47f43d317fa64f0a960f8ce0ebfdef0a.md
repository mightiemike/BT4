The comment confirms: "RescueFundsOnSourceChain event emitted by UniversalGatewayPC when a **user initiates** a rescue on the source chain" [1](#0-0) , meaning `rescue_funds` on `UniversalGatewayPC` is a user-reachable call, not admin-gated — matching the "unprivileged external attacker" scope.

### Title
Rescue-outbound creation trusts an unauthenticated user-supplied PRC20 in `AttachRescueOutboundFromReceipt`, allowing amount/asset mismatch (analog of MigratorZap `v2InventoryToXNFT` id/token mismatch) - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`AttachRescueOutboundFromReceipt` builds a `RESCUE_FUNDS` outbound from a user-triggered `RescueFundsOnSourceChain` event without verifying that the event's `PRC20` field is the same asset that was originally locked for the referenced `UniversalTxId`. This mirrors the MigratorZap bug where `vaultIdV2` and `vTokenV2` are two independently attacker-supplied identifiers that are expected to match but are never cross-checked, letting mismatched inputs corrupt accounting.

### Finding Description
`AttachRescueOutboundFromReceipt` decodes `RescueFundsOnSourceChain(universalTxId, prc20, chainNamespace, sender, txType, gasFee, gasPrice, gasLimit)` [2](#0-1) . Both `universalTxId` and `prc20` are attacker/user-supplied arguments to the on-chain `rescue_funds` gateway call (per the doc comment, the event fires "when a user initiates a rescue"). The keeper then:

1. Loads the *original* UTX solely by `universalTxId` and checks only generic rescue-eligibility (CEA deposit failed, or auto-revert reverted) [3](#0-2) .
2. Resolves the *external asset address* by looking up `event.PRC20` in the registry for `originalUtx.InboundTx.SourceChain` — the PRC20 is never checked against `originalUtx.InboundTx.AssetAddr`, the asset that was actually locked for this specific UTX [4](#0-3) .
3. Constructs the outbound using `Amount: originalUtx.InboundTx.Amount` (denominated in the *original* asset) together with `ExternalAssetAddr: tokenCfg.Address` and `Prc20AssetAddr: event.PRC20` (derived from the *attacker-chosen* PRC20) [5](#0-4) .

Exactly as in the MigratorZap report — where `vaultIdV2` (identifies the balance/shares to withdraw) and `vTokenV2` (identifies the token address used for the follow-up transfer) are independent, unchecked inputs — here `universalTxId` (identifies the stuck amount) and `PRC20` (identifies the token that gets instructed for release on the source chain) are independent, unchecked inputs. Any registered PRC20 on the same source chain can be substituted, producing an outbound instructing the TSS/relayer to release `originalUtx.InboundTx.Amount` units denominated in a different registered token than the one that was actually locked.

### Impact Explanation
This can misroute or over/under-value fund releases on the external chain: an attacker who has one eligible rescue UTX (e.g., a small stuck deposit) can trigger a rescue outbound quoting an unrelated token's address paired with the stuck UTX's raw amount value, corrupting `OutboundTx.ExternalAssetAddr`/`Prc20AssetAddr`/`Amount` semantics. Depending on how the TSS-signed outbound is executed on the source-chain gateway/vault, this can result in withdrawal of the wrong asset or an amount that doesn't correspond to what's actually escrowed for that PRC20, i.e., unauthorized release of protocol-controlled funds — squarely in the "unauthorized release ... of user or protocol-controlled funds" and "corruption of ... token mapping" impact categories.

### Likelihood Explanation
Medium: requires the attacker to have (or create) at least one UTX in the specific rescue-eligible state (CEA deposit FAILED, or auto-revert REVERTED) — a state reachable through ordinary deposit/inbound flows — and then to call the on-chain `rescue_funds` function with a `PRC20` argument different from the one tied to that UTX. Both conditions are reachable by an unprivileged user through documented, user-initiated flows, but exploitation value depends on downstream vault accounting on the specific source chain/vault the mismatched PRC20 maps to.

### Recommendation
In `AttachRescueOutboundFromReceipt`, cross-validate `event.PRC20` against the token config that maps to `originalUtx.InboundTx.AssetAddr` (i.e., require `tokenCfg.NativeRepresentation.ContractAddress == event.PRC20` for the resolved token, or resolve the token config directly from `originalUtx.InboundTx.AssetAddr` and only use `event.PRC20` for sanity-checking, not for reconstructing outbound asset routing). Reject the rescue if the PRC20 in the event doesn't correspond to the asset actually locked for that `universalTxId`.

### Proof of Concept
Conceptual PoC (cannot be executed without a running devnet/contract source for `UniversalGatewayPC.rescue_funds`):
1. Attacker deposits token A on source chain via a CEA inbound that fails on Push Chain (deposit PCTx status = FAILED), making the UTX rescue-eligible [6](#0-5) .
2. Attacker calls `rescue_funds` on `UniversalGatewayPC` passing this UTX's `universalTxId` but with `PRC20` = the registered PRC20 address of token B (a different, unrelated but registered token on the same source chain).
3. `AttachRescueOutboundFromReceipt` resolves `tokenCfg` for token B via `GetTokenConfigByPRC20` [7](#0-6)  and creates an outbound with `Amount = originalUtx.InboundTx.Amount` (token A's amount) but `ExternalAssetAddr`/`Prc20AssetAddr` referencing token B.
4. This mismatched outbound is voted on and eventually TSS-signed/relayed to the source chain, releasing token B in an amount computed for token A.

I was unable to inspect the Solidity source of `UniversalGatewayPC.rescue_funds()` itself (not indexed in this scan) to confirm there is no on-chain check binding `prc20` to the `universalTxId`'s original asset; if such on-chain binding exists, this finding would be mitigated at the contract layer rather than the module layer. A Devin session with full repo/contract access would be needed to confirm the Solidity-side validation.

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L101-112)
```go
// RescueFundsOnSourceChainEvent holds decoded data from the RescueFundsOnSourceChain
// event emitted by UniversalGatewayPC when a user initiates a rescue on the source chain.
type RescueFundsOnSourceChainEvent struct {
	UniversalTxId  string   // 0x-prefixed bytes32 — the original UTX whose funds are stuck
	PRC20          string   // 0x-prefixed address — PRC20 token whose counterpart is locked
	ChainNamespace string   // source chain namespace (e.g. "eip155")
	Sender         string   // 0x-prefixed address — user who initiated the rescue
	TxType         TxType   // always TxType_RESCUE_FUNDS
	GasFee         *big.Int // gas fee charged (in gas-token units)
	GasPrice       *big.Int // gas price on the source chain
	GasLimit       *big.Int // gas limit used for the rescue execution
}
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L114-136)
```go
// DecodeRescueFundsOnSourceChainFromLog decodes a RescueFundsOnSourceChain event log.
//
// Event signature:
//
//	RescueFundsOnSourceChain(bytes32 indexed universalTxId, address indexed prc20,
//	  string chainNamespace, address indexed sender, uint8 txType,
//	  uint256 gasFee, uint256 gasPrice, uint256 gasLimit)
//
// Topics: [sig, universalTxId, prc20, sender]
// Data:   [chainNamespace, txType, gasFee, gasPrice, gasLimit]
func DecodeRescueFundsOnSourceChainFromLog(log *evmtypes.Log) (*RescueFundsOnSourceChainEvent, error) {
	if len(log.Topics) < 4 {
		return nil, fmt.Errorf("RescueFundsOnSourceChain: need 4 topics, got %d", len(log.Topics))
	}
	if strings.ToLower(log.Topics[0]) != strings.ToLower(RescueFundsOnSourceChainEventSig) {
		return nil, fmt.Errorf("not a RescueFundsOnSourceChain event")
	}

	event := &RescueFundsOnSourceChainEvent{
		UniversalTxId: log.Topics[1], // bytes32 as 0x-prefixed hex
		PRC20:         common.HexToAddress(log.Topics[2]).Hex(),
		Sender:        common.HexToAddress(log.Topics[3]).Hex(),
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L228-262)
```go
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

**File:** x/uexecutor/keeper/create_outbound.go (L302-320)
```go
		logIndex := fmt.Sprintf("%d", lg.Index)
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
