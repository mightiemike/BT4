## Analysis

The code at `x/uexecutor/keeper/create_outbound.go:284-320` builds the rescue `OutboundTx` and resolves the asset to release purely from the attacker-supplied `event.PRC20` field, with no cross-check against the token that was actually locked by the original inbound.

`DecodeRescueFundsOnSourceChainFromLog` decodes `PRC20` straight from `log.Topics[2]`, an indexed event parameter that originates from a user-supplied call argument on the `UniversalGatewayPC` contract [1](#0-0) . `AttachRescueOutboundFromReceipt` then resolves this value through `GetTokenConfigByPRC20(ctx, originalUtx.InboundTx.SourceChain, event.PRC20)` and directly assigns the result to `ExternalAssetAddr`/`Prc20AssetAddr` on the constructed rescue outbound, with the amount taken from `originalUtx.InboundTx.Amount` [2](#0-1) . At no point is `event.PRC20` compared against `originalUtx.InboundTx.AssetAddr` (the token that was actually locked for that specific UTX) — only the *chain* is checked, not that the PRC20 is the counterpart of the original inbound's asset.

The eligibility checks preceding this only validate that the referenced UTX had a failed CEA deposit or a reverted auto-revert [3](#0-2) ; they never re-validate the token identity. Since `originalUtxId` is derived from `event.UniversalTxId`, which is also an attacker-supplied topic (with the constraint that it must reference a UTX belonging to the caller's own failed/stuck inbound to pass the earlier eligibility gates), an attacker who owns one stuck/failed UTX for asset A on a given source chain can submit a rescue with `PRC20` = the PRC20 counterpart of a different, unrelated, but registered asset B on that same source chain. The keeper will happily attach a `RESCUE_FUNDS` outbound with `ExternalAssetAddr` = asset B's address while the `Amount` still reflects the original inbound's numeric amount for asset A.

This corrupted outbound is subsequently signed and executed by TSS on the source chain Vault via the `rescueFunds(bytes32,bytes32,address,uint256,(address,bytes))` call built in the universal client's EVM tx builder [4](#0-3) , meaning an honest TSS/relayer following normal protocol will release asset B (belonging to other users' locked funds in the Vault) instead of the originally locked asset A.

## Verdict

This is a real accounting-corruption vulnerability reachable by an ordinary user through a standard rescue-fund flow — the missing cross-check between `event.PRC20` and the UTX's originally recorded asset (`originalUtx.InboundTx.AssetAddr`) allows a user to redirect the rescue outbound to release a different, unrelated PRC20/token pair from the source-chain Vault.

### Title
Rescue outbound resolves `ExternalAssetAddr` from unchecked attacker-controlled `PRC20` topic instead of the UTX's original locked asset - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`AttachRescueOutboundFromReceipt` builds a `RESCUE_FUNDS` outbound by resolving the token config solely from the attacker-controlled `PRC20` indexed event topic, without verifying that this PRC20 corresponds to the asset that was actually locked for the referenced UTX (`originalUtx.InboundTx.AssetAddr`).

### Finding Description
`DecodeRescueFundsOnSourceChainFromLog` extracts `PRC20` directly from an indexed log topic that traces back to a user-supplied argument on the Push Chain gateway contract [5](#0-4) . `AttachRescueOutboundFromReceipt` uses this value, together with the source chain recorded on the referenced UTX, to look up a token config via `GetTokenConfigByPRC20`, and assigns the result as `ExternalAssetAddr`/`Prc20AssetAddr` on the new rescue `OutboundTx`, while `Amount` is copied from the original inbound [2](#0-1) . Nowhere in this function or in the preceding eligibility checks [3](#0-2)  is `event.PRC20` compared to the token originally associated with that inbound (`originalUtx.InboundTx.AssetAddr`). As long as the referenced UTX is genuinely stuck/failed (satisfying the eligibility gate) and the attacker-chosen PRC20 is registered for the same source chain (any registered token qualifies, not just the one tied to that specific UTX), the rescue outbound will carry the wrong external asset address.

### Impact Explanation
The corrupted `ExternalAssetAddr` flows into the outbound signing/execution pipeline and is used to build the `rescueFunds(...)` call executed by TSS on the source-chain Vault [4](#0-3) . Honest TSS/validators, following the protocol as designed, will release whatever asset the attacker specified rather than the asset genuinely locked for that UTX, resulting in unauthorized release of protocol/Vault-held funds belonging to a different token pool — a direct fund-loss and accounting-corruption impact.

### Likelihood Explanation
The prerequisite (having at least one genuinely stuck/failed UTX) is achievable by any ordinary user simply by depositing with an inbound that fails or gets reverted — a normal, unprivileged interaction. From there, crafting a rescue call with an arbitrary but chain-registered PRC20 address is a single parameter choice, making this readily reachable by any unprivileged user.

### Recommendation
In `AttachRescueOutboundFromReceipt`, after decoding `event.PRC20`, cross-validate it against the token actually associated with `originalUtx.InboundTx` (e.g., resolve the token config for `originalUtx.InboundTx.AssetAddr` and require `event.PRC20` to match its registered PRC20 counterpart) before using it to build the outbound's `ExternalAssetAddr`/`Prc20AssetAddr`. Reject the rescue event if the PRC20 does not match the UTX's originally recorded asset.

### Proof of Concept
1. Register two distinct token configs on the same source chain (e.g., `eip155:11155111`): Token A ↔ PRC20-A, Token B ↔ PRC20-B.
2. Submit an inbound using Token A that fails to deposit (CEA path), producing a UTX with `InboundTx.AssetAddr = TokenA` and a FAILED first `PcTx`, per the eligibility flow in [6](#0-5) .
3. Emit a `RescueFundsOnSourceChain` log referencing this UTX's id but with the `PRC20` topic set to PRC20-B (Token B's PRC20), as constructed in the test helper `buildRescueFundsLog` [7](#0-6) .
4. Call `AttachRescueOutboundFromReceipt`; observe that it succeeds and produces a `RESCUE_FUNDS` outbound with `ExternalAssetAddr = TokenB` and `Prc20AssetAddr = PRC20-B`, despite the original locked asset being Token A — demonstrating the missing PRC20-to-original-asset cross-check.

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L124-136)
```go
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

**File:** x/uexecutor/keeper/create_outbound.go (L284-313)
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

		// Rescued funds go to the original revert recipient (or the sender as fallback).
		recipient := originalUtx.InboundTx.Sender
		if originalUtx.InboundTx.RevertInstructions != nil &&
			originalUtx.InboundTx.RevertInstructions.FundRecipient != "" {
			recipient = originalUtx.InboundTx.RevertInstructions.FundRecipient
		}

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
```

**File:** universalClient/chains/evm/tx_builder.go (L399-401)
```go
	case "rescueFunds":
		// Vault: rescueFunds(bytes32,bytes32,address,uint256,(address,bytes))
		return "rescueFunds(bytes32,bytes32,address,uint256,(address,bytes))"
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L32-72)
```go
func buildRescueFundsLog(
	t *testing.T,
	utxId string, // UTX key (64-char hex, no 0x prefix)
	prc20Addr common.Address,
	senderAddr common.Address,
	chainNamespace string,
	gasFee, gasPrice, gasLimit *big.Int,
) *evmtypes.Log {
	t.Helper()

	stringType, _ := abi.NewType("string", "", nil)
	uint8Type, _ := abi.NewType("uint8", "", nil)
	uint256Type, _ := abi.NewType("uint256", "", nil)

	args := abi.Arguments{
		{Type: stringType},  // chainNamespace
		{Type: uint8Type},   // txType (RESCUE_FUNDS = 4)
		{Type: uint256Type}, // gasFee
		{Type: uint256Type}, // gasPrice
		{Type: uint256Type}, // gasLimit
	}
	data, err := args.Pack(chainNamespace, uint8(4), gasFee, gasPrice, gasLimit)
	require.NoError(t, err)

	// UTX ID is stored as a bytes32 topic: "0x" + the 64-char hex UTX key.
	utxIdTopic := "0x" + utxId

	gwPCAddr := utils.GetDefaultAddresses().UniversalGatewayPCAddr

	return &evmtypes.Log{
		Address: gwPCAddr.Hex(),
		Topics: []string{
			uexecutortypes.RescueFundsOnSourceChainEventSig,
			utxIdTopic,
			common.BytesToHash(prc20Addr.Bytes()).Hex(),  // indexed prc20
			common.BytesToHash(senderAddr.Bytes()).Hex(), // indexed sender
		},
		Data:    data,
		Removed: false,
	}
}
```
