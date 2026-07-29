### Title
RESCUE_FUNDS outbound trusts attacker-supplied `prc20` from `RescueFundsOnSourceChain` event instead of validating it against the original inbound's asset - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`AttachRescueOutboundFromReceipt` builds a `RESCUE_FUNDS` outbound whose `Amount` and `Recipient` are safely pinned to the original, already-stored `UniversalTx` (so an attacker cannot redirect funds or inflate the amount), but the `Prc20AssetAddr` / `ExternalAssetAddr` fields are taken directly from the `RescueFundsOnSourceChain` event log — a value that is fully controlled by whoever triggers the event — without cross-checking it against the original inbound's `AssetAddr`/`Prc20` token.

### Finding Description
In `x/uexecutor/keeper/create_outbound.go`, `AttachRescueOutboundFromReceipt` decodes a `RescueFundsOnSourceChain` log: [1](#0-0) 

It correctly re-derives `recipient` and `amount` from the trusted, previously-stored `originalUtx.InboundTx` fields: [2](#0-1) 

But it resolves the token configuration, and populates `Prc20AssetAddr`/`ExternalAssetAddr`, purely from `event.PRC20` — a value taken from the log's indexed `prc20` topic — with no check that this matches the PRC20/asset that was actually used in the original inbound (`originalUtx.InboundTx.AssetAddr` / the deposit's real PRC20): [3](#0-2) 

Compare this to the ordinary outbound-building path (`BuildOutboundsFromReceipt`), which resolves `tokenCfg` the same way from `event.Token`, but there the `Amount`, `Recipient`, and `Token` all originate from the *same* attacker/user-authored `UniversalTxOutbound` event that was just executed on Push Chain in that very transaction — i.e., a single, atomic, self-consistent event drives the whole outbound: [4](#0-3) 

In the rescue path, by contrast, the outbound is a hybrid: `amount`/`recipient` come from a different data source (`originalUtx`, produced by validator consensus long before) than `prc20` (comes from the event just observed). `GetTokenConfigByPRC20` looks up a `TokenConfig` purely from the PRC20 address without regard to what the original deposit actually was: [5](#0-4) 

Because the keeper never asserts `event.PRC20 == originalUtx.InboundTx.AssetAddr` (or the PRC20 equivalent), it is possible for the resulting outbound to carry a `Prc20AssetAddr`/`ExternalAssetAddr` pair that maps to a completely different registered token than what was actually deposited/stuck, while the `Amount` field still reflects the original (possibly much smaller or larger, in different decimals) token's amount.

### Impact Explanation
If the `UniversalGatewayPC` contract's rescue-initiation function on Push Chain allows the caller to supply (or otherwise does not strictly bind) the `prc20` parameter used in the emitted event to the token actually associated with the stuck `universalTxId`, this mismatch lets the resulting `RESCUE_FUNDS` outbound instruct UVs to release `originalUtx.InboundTx.Amount` units of a *different* external asset than what was locked. This can misroute or drain a higher-value token's escrow using a low-value inbound's amount, corrupting cross-chain token accounting — squarely in the "Registry and accounting path" invariant (token mapping must not misroute value or attach the wrong asset semantics).

However, this repository does not contain the `UniversalGatewayPC` Solidity source (it is a pre-deployed/system EVM contract referenced only by address via `uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"]`), so I cannot confirm from this codebase alone whether the contract itself constrains `prc20` to the value already associated with `universalTxId` before emitting the event. If the contract enforces that binding on-chain, this keeper-side gap is not independently exploitable by an unprivileged actor.

### Likelihood Explanation
Uncertain/Low-to-Medium: the vulnerable code path (missing cross-validation of `event.PRC20` against the original deposit's asset) is real and present in scoped Go code, but whether it is reachable by an unprivileged attacker depends entirely on the out-of-scope, unavailable `UniversalGatewayPC` contract's own validation of the `prc20` argument when a user "initiates a rescue." I could not verify this contract's logic with the tools available.

### Recommendation
In `AttachRescueOutboundFromReceipt`, validate that `event.PRC20` matches the token originally associated with `originalUtx.InboundTx` (e.g., resolve `originalUtx.InboundTx.AssetAddr`'s PRC20 counterpart via the registry and compare, or simply reject rescues where `event.PRC20` differs from what the original UTX recorded) before trusting it to select `tokenCfg`/`ExternalAssetAddr`, so the rescue outbound is fully derived from the trusted historical `UniversalTx`, never from attacker-observable log fields for anything except non-value-bearing metadata (gas price/limit).

### Proof of Concept
Not constructible with confidence from this repository alone: exploitation requires the ability to make `UniversalGatewayPC` emit `RescueFundsOnSourceChain` with an `event.PRC20` that differs from the original inbound's token for a given `universalTxId`. That gating logic lives in the (not indexed/available) Solidity contract, so a concrete PoC cannot be built without access to `UniversalGatewayPC`'s source. The Go-side gap itself is demonstrable by unit-testing `AttachRescueOutboundFromReceipt` with a `buildRescueFundsLog` (as in `test/integration/uexecutor/rescue_funds_test.go`) using a `prc20Addr` different from the original inbound's `AssetAddr`/PRC20 and observing that no error occurs and the outbound is built with the mismatched `Prc20AssetAddr`.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-91)
```go
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L220-237)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L284-320)
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
			TxType:            types.TxType_RESCUE_FUNDS,
			OutboundStatus:    types.Status_PENDING,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: logIndex,
			},
		}
```

**File:** x/uregistry/keeper/keeper.go (L240-276)
```go
// GetTokenConfigByPRC20 looks up a token config by PRC20 address via the
// PRC20Index (O(1)). Returns ErrNotFound if the registered chain doesn't match.
func (k Keeper) GetTokenConfigByPRC20(
	ctx context.Context,
	chain string,
	prc20Addr string,
) (types.TokenConfig, error) {

	if strings.TrimSpace(prc20Addr) == "" {
		return types.TokenConfig{}, fmt.Errorf("prc20 address is empty")
	}
	// Same canonical form as the index function, so any case variant hits the row.
	prc20Addr = canonicalPRC20(prc20Addr)

	// PRC20 addresses are globally unique by construction; MatchExact returns at most one.
	iter, err := k.TokenConfigs.Indexes.PRC20Index.MatchExact(ctx, prc20Addr)
	if err != nil {
		return types.TokenConfig{}, err
	}
	defer iter.Close()

	for ; iter.Valid(); iter.Next() {
		pk, err := iter.PrimaryKey()
		if err != nil {
			return types.TokenConfig{}, err
		}
		cfg, err := k.TokenConfigs.Get(ctx, pk)
		if err != nil {
			return types.TokenConfig{}, err
		}
		if cfg.Chain == chain {
			return cfg, nil
		}
	}

	return types.TokenConfig{}, collections.ErrNotFound
}
```
