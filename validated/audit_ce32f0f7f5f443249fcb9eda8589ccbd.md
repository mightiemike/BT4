## Analysis

The Boba report's bug class is: **a fee/exchange rate that is refreshed only periodically by an off-chain/on-chain oracle is used to price a value-transferring operation, and the protocol absorbs the difference when the real-time market price diverges before the next refresh — with no mechanism to claw back the shortfall.**

The closest reachable analog in Push Chain is the **destination-chain gas-fee pricing and refund asymmetry** in the `x/uexecutor` outbound pipeline.

### Mechanism

1. Universal validators periodically vote an observed gas price for each destination chain via `MsgVoteChainMeta`, driven by `ChainMetaOracle.fetchAndVoteChainMeta` on a fixed interval (default 30s, configurable) in both `universalClient/chains/evm/chain_meta_oracle.go` and `universalClient/chains/svm/chain_meta_oracle.go`. [1](#0-0) 

2. When an outbound is created, `UniversalCore.getOutboundTxGasAndFees` returns `gasToken`, `gasFee`, `gasPrice`, `gasLimit` for the destination chain, and these are recorded on the `OutboundTx` (via `GetOutboundTxGasAndFees` / `buildRevertOutbound`, and via the `UniversalTxOutbound` event decoded in `BuildOutboundsFromReceipt`). [2](#0-1) [3](#0-2) 

3. This `gasFee` is the amount pre-collected from the user (in PRC20/native asset) to cover the eventual real-world broadcast cost of the outbound on the destination chain.

4. When validators later vote `MsgVoteOutbound` with the actual `GasFeeUsed` observed on the destination chain, `applyGasRefund` in `x/uexecutor/keeper/outbound.go` only handles the surplus case:
```go
// No excess gas to refund
if gasFee.Cmp(gasFeeUsed) <= 0 {
    return
}
``` [4](#0-3) 

There is **no corresponding path that recovers a deficit** when `gasFeeUsed > gasFee` (i.e., when real gas cost on the destination chain exceeds what was pre-charged based on the last periodically-voted `ChainMeta` price). The outbound is still broadcast/executed regardless, and the shortfall is silently absorbed rather than billed back to the sender.

### Why this matches the Boba bug class

- The `gasFee` charged to the user is a value derived from a **periodically-refreshed oracle price** (`ChainMeta.Prices`/`MedianIndex`, refreshed on the `ChainMetaOracle` interval), exactly like Boba's `marketPriceRatio` polled every five minutes.
- Between votes, the real-world gas price on the destination chain can spike (network congestion, MEV, base-fee volatility) — the same "high volatility" window the Boba report describes.
- An unprivileged user submitting inbound transactions with outbound legs (`FUNDS`, `GAS_AND_PAYLOAD`, `FUNDS_AND_PAYLOAD`, `INBOUND_REVERT`) during such a window pays a `gasFee` based on the stale, lower `ChainMeta` price, while the network/TSS-funded relayer must still pay the real, higher cost to broadcast the outbound — a one-sided asymmetry (only over-collection is refunded, never under-collection recovered) that lets repeated/volume-scaled use drain protocol-controlled gas funds.

### Caveats / why I'm not fully certain this rises to a standalone finding

- I did not find (within index limits) the exact code that funds the TSS/relayer wallet that actually broadcasts outbound transactions on the destination chain, so I cannot show the precise balance/account that gets drained — this would need to be confirmed in a live session.
- The magnitude of exploitable divergence is bounded by the chain-meta vote interval (default 30s) and by destination-chain gas volatility over that window, which is smaller than Boba's ~5-minute window, but the same class of risk exists.
- This is an architectural/economic gap rather than a memory-safety or authorization bypass — it fits the "corruption of gas fee accounting / refund accounting" allowed-impact category, but the actual attacker profit per transaction depends on external gas-market conditions the attacker doesn't fully control (unlike a pure on-chain arbitrage).

Given these caveats, I'd frame this as a plausible but **not fully proven, root-caused vulnerability** — it warrants deeper investigation (with full repo access) into where the deficit-covering funds originate and whether attacker-controlled batching/timing can make the exploit reliably profitable, before treating it as a confirmed drain.

### Citations

**File:** universalClient/chains/evm/chain_meta_oracle.go (L58-69)
```go
// fetchAndVoteChainMeta periodically fetches gas price and votes on it
func (g *ChainMetaOracle) fetchAndVoteChainMeta(ctx context.Context) {
	defer g.wg.Done()

	// Get gas oracle fetch interval from config
	interval := g.getChainMetaOracleFetchInterval()
	if interval <= 0 {
		interval = 30 * time.Second
	}

	ticker := time.NewTicker(interval)
	defer ticker.Stop()
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-63)
```go
// GetOutboundTxGasAndFees calls UniversalCore.getOutboundTxGasAndFees(prc20, gasLimitWithBaseLimit)
// to get gasToken, gasFee, protocolFee, gasPrice, and chainNamespace.
// Pass gasLimitWithBaseLimit=0 to use the contract's baseLimit.
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L178-196)
```go
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}
```
