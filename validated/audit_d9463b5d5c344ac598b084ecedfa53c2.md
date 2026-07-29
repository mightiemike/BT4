## Analysis

The reported Pyth `tokenPriceUSDT()` bug is a classic "oracle value consumed without checking `publishTime` freshness" pattern. Push Chain has a directly analogous oracle: the `ChainMeta` gas-price oracle (`x/uexecutor/keeper/chain_meta.go`), which Universal Validators vote on and which is written into the `UniversalCore` EVM contract via `setChainMeta`. That contract even tracks a `timestampObservedAtByChainNamespace` (its own `publishTime`-equivalent) [1](#0-0) , but none of the consumers actually check it.

### Title
Stale on-chain gas price consumed for outbound fee/refund accounting with no freshness check - (File: `x/uexecutor/keeper/gas_fee.go`, `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`VoteChainMeta` only enforces a 300-second staleness window when *aggregating votes* into the median it writes on-chain [2](#0-1) [3](#0-2) . Once a median is committed via `CallUniversalCoreSetChainMeta`, the stored `gasPriceByChainNamespace` value in the `UniversalCore` contract persists indefinitely if validators stop voting for that chain (low-traffic external chain, oracle worker outage, etc.) — there is no expiry on the *stored* value itself. Every downstream consumer — `GetGasPriceByChain`, `GetOutboundTxGasAndFees`, and `GetGasFeeInfoForRevertOutbound` — reads this value via plain `CallEVM` with no comparison against `timestampObservedAtByChainNamespace` or current block time [4](#0-3) [5](#0-4) .

### Finding Description
The `gasPriceByChainNamespace`/`getOutboundTxGasAndFees` values are baked directly into every outbound's `GasPrice`/`GasFee` fields at creation time — both for normal outbounds spawned from `UniversalTxOutbound` events [6](#0-5)  and for `INBOUND_REVERT` outbounds built via `buildRevertOutbound` → `GetGasFeeInfoForRevertOutbound` → `GetOutboundTxGasAndFees` [7](#0-6) . These fields subsequently drive `applyGasRefund`, which computes `gasFee - gasFeeUsed` and pays the difference back to the user out of the PRC20/gas-token pool [8](#0-7) .

Because no code path checks the age of the on-chain gas price before using it for this fee/refund math, an ordinary user who triggers an inbound (any `FUNDS_AND_PAYLOAD`/`PAYLOAD` inbound producing an outbound, or an inbound that will revert) against a chain whose gas-price oracle has gone stale (validators stopped voting, e.g. an infrequently-used destination chain) will have `GasFee`/`GasPrice` computed from an arbitrarily old on-chain value rather than the actual current destination-chain conditions.

### Impact Explanation
This corrupts gas-fee and refund accounting (an explicitly in-scope invariant): if the stale on-chain price is far below real destination gas prices, relayers/broadcasters get under-compensated for real broadcast cost while the protocol still records/refunds based on the stale number; if the stale price is far above real prices, `applyGasRefund` will over-refund the difference back to users out of protocol-held gas-token funds, since the entire `gasFee` baseline used in the refund math is derived from that same stale oracle read. Either direction is a fund-accounting corruption reachable purely by an ordinary user submitting a default inbound/outbound-triggering transaction — no privileged actor is required.

### Likelihood Explanation
Likelihood is proportional to how often each observed chain accrues fresh votes. Low-activity destination chains (or chains where the off-chain `ChainMetaOracle` client experiences downtime) can go stale for long periods since nothing in the write or read path forces re-voting or blocks consumption of an old value. Any user transaction routing to such a chain during that window silently uses the stale price with no error or fallback.

### Recommendation
Add a staleness check on the read side: have `GetGasPriceByChain` / `GetOutboundTxGasAndFees` (or the underlying `getOutboundTxGasAndFees`/`gasPriceByChainNamespace` contract calls) also read `timestampObservedAtByChainNamespace` and compare it against `sdkCtx.BlockTime()` with an explicit tolerance (mirroring the existing `chainMetaVoteStalenessSeconds` constant used on the write side). If stale, either revert the outbound-creation flow with an explicit error, or fall back to a safe default/pause outbound creation for that chain until a fresh vote lands.

### Proof of Concept
1. Onboard an external chain with low UV voting frequency; let 3 UVs bootstrap `ChainMeta` for it once (`LastAppliedChainHeight > 0`).
2. Stop all `ChainMetaOracle` voting for that chain (simulate an oracle worker crash or simply a quiet chain with no further `MsgVoteChainMeta` traffic).
3. Wait well beyond `chainMetaVoteStalenessSeconds` (300s) — the stored `gasPriceByChainNamespace` value in `UniversalCore` remains unchanged and keeps returning the old price indefinitely, since staleness is only applied to which *votes* enter the median calculation, not to consumption of the already-committed value.
4. Submit an ordinary inbound (e.g. `FUNDS_AND_PAYLOAD`) that creates an outbound to that chain, or one that later reverts. `BuildOutboundsFromReceipt` / `buildRevertOutbound` populate `GasFee`/`GasPrice` from the stale on-chain value with no freshness check [7](#0-6) .
5. Observe that the outbound's fee accounting (and any later `applyGasRefund`) is computed entirely from the stale value, independent of real current gas conditions on the destination chain.

### Citations

**File:** x/uexecutor/types/abi.go (L363-368)
```go
      "type": "function",
      "name": "timestampObservedAtByChainNamespace",
      "inputs": [{ "name": "", "type": "string", "internalType": "string" }],
      "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
      "stateMutability": "view"
    },
```

**File:** x/uexecutor/keeper/chain_meta.go (L16-19)
```go
const (
	// chainMetaVoteStalenessSeconds is the maximum age (in seconds) of a stored vote
	// that is still eligible to be included in the median calculation.
	chainMetaVoteStalenessSeconds uint64 = 300
```

**File:** x/uexecutor/keeper/chain_meta.go (L110-127)
```go
	// Build a filtered pool: only votes stored within the staleness window.
	type voteSnapshot struct {
		price       uint64
		chainHeight uint64
	}
	var fresh []voteSnapshot
	for i := range entry.Signers {
		if entry.StoredAts[i] > now {
			continue // clock skew guard — skip future-stamped votes
		}
		age := now - entry.StoredAts[i]
		if age <= chainMetaVoteStalenessSeconds {
			fresh = append(fresh, voteSnapshot{
				price:       entry.Prices[i],
				chainHeight: entry.ChainHeights[i],
			})
		}
	}
```

**File:** x/uexecutor/keeper/evm.go (L349-371)
```go
// GetGasPriceByChain reads the gas price for a chain from the UniversalCore contract.
func (k Keeper) GetGasPriceByChain(ctx sdk.Context, chainNamespace string) (*big.Int, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "gasPriceByChainNamespace", chainNamespace)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call gasPriceByChainNamespace")
	}

	results, err := abi.Methods["gasPriceByChainNamespace"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack gasPriceByChainNamespace result")
	}

	return results[0].(*big.Int), nil
}
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-64)
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
}
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

**File:** x/uexecutor/keeper/build_revert_outbound.go (L38-53)
```go
	// Fetch gas fields from UniversalCore.getOutboundTxGasAndFees(prc20, 0)
	// 0 means use the contract's baseLimit for this chain
	gasToken, gasFee, gasPrice, gasLimit, err := k.GetGasFeeInfoForRevertOutbound(sdkCtx, tokenCfg.NativeRepresentation.ContractAddress)
	if err != nil {
		k.Logger().Warn("failed to fetch gas fee info for revert outbound, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"prc20", tokenCfg.NativeRepresentation.ContractAddress,
			"error", err,
		)
		return outbound
	}

	outbound.GasToken = gasToken
	outbound.GasFee = gasFee
	outbound.GasPrice = gasPrice
	outbound.GasLimit = gasLimit
```

**File:** x/uexecutor/keeper/outbound.go (L174-230)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
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

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
```
