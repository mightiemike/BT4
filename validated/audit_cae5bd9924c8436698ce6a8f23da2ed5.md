### Title
Stale gas-price oracle used for outbound gas-fee and refund accounting with no on-chain "last observed" liveness check - ([File: x/uexecutor/keeper/gas_fee.go])

### Summary
Push Chain's `ChainMeta` gas-price oracle (analogous to the Chainlink price feed used by `GLPOracle`) is written on-chain via `CallUniversalCoreSetChainMeta` [1](#0-0)  and read back via `GetGasPriceByChain`/`GetOutboundTxGasAndFees` whenever an outbound tx's gas fee, gas price, or gas limit needs to be computed. The `UniversalCore` contract exposes `timestampObservedAtByChainNamespace`, a per-chain "last observed" timestamp getter [2](#0-1) , but no Go consumer of the gas price ever calls it or otherwise checks the age of the stored price before using it, exactly the missing "is the feed still live" check that the Chainlink L2-sequencer report flags.

### Finding Description
`VoteChainMeta` only bounds staleness at the *voting* stage — a vote is discarded from the median computation if it is older than `chainMetaVoteStalenessSeconds` (300s) [3](#0-2) . It does not, however, prevent the *last successfully written* on-chain value from persisting indefinitely if the external chain becomes inactive (RPC dies, no `universalClient/chains/*/chain_meta_oracle.go` fresh votes arrive, quorum of fresh votes never re-forms) [4](#0-3) .

Downstream, `GetGasPriceByChain` and `GetOutboundTxGasAndFees` simply call the `UniversalCore` contract and use whatever `gasPriceByChainNamespace`/`getOutboundTxGasAndFees` currently returns, with no check of `timestampObservedAtByChainNamespace` or any freshness bound: [5](#0-4) 

This value is then used directly to populate `OutboundTx.GasFee/GasPrice/GasLimit` for revert outbounds [6](#0-5)  and drives `applyGasRefund`, which compares the (possibly stale) `GasFee` against the actually observed `GasFeeUsed` and, if `GasFee > GasFeeUsed`, mints/refunds the difference in PRC20/native gas token back to the user via `CallUniversalCoreRefundUnusedGas` [7](#0-6) .

Because there is no liveness/staleness check on the price at the point of consumption (unlike the vote-ingestion staleness window), the accounting invariant "refund reflects the real-time gas cost" can silently diverge from reality whenever the external chain's price/height feed stops updating — with no alarm, revert, or fallback in the read path.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting" and "unauthorized ... refund of user or protocol-controlled funds" in the allowed impact set. If the stored gas price is stale-high relative to the real current price at execution time, any unprivileged user whose inbound produces an outbound (or revert-outbound) benefits from an inflated `GasFee` baseline, and `applyGasRefund` will pay out the inflated excess from protocol reserves. Conversely, a stale-low price undercharges/underfunds outbound relayer execution, risking outbound failures/fund mismanagement. Neither scenario requires a malicious validator or relayer — it is purely a function of external chain feed staleness combined with the absence of a staleness guard on the consuming side, reachable by any user submitting ordinary inbound transactions during the stale window.

### Likelihood Explanation
Likelihood depends on how often a given external chain's gas price actually goes silent long enough for the stored value to significantly diverge from the live market price (e.g., RPC provider outage, chain congestion event, or a low-traffic chain where votes lapse). This is plausible under ordinary operational conditions (no attacker collusion needed) and is exactly the operational scenario the missing-sequencer-check bug class targets — an unprivileged user only needs to time an inbound/outbound-producing transaction to profit or to cause under-funded outbound execution.

### Recommendation
Before using `gasPriceByChainNamespace`/`getOutboundTxGasAndFees` output for fee/refund accounting, read and check `timestampObservedAtByChainNamespace(chainNamespace)` (or an equivalent stored-at value already tracked in `ChainMeta.StoredAts`) against a maximum allowed age, mirroring the Chainlink L2-sequencer pattern of rejecting/falling back when the feed is stale, rather than only enforcing staleness at vote-ingestion time.

### Proof of Concept
1. An external chain's `ChainMetaOracle` in `universalClient` stops submitting fresh votes (RPC outage, no new blocks, or simply no active Universal Validator infra for that chain) for longer than a market-moving period.
2. `UniversalCore`'s `gasPriceByChainNamespace` for that chain remains frozen at the last value written by `CallUniversalCoreSetChainMeta`, while the real gas price on the external chain has since moved significantly.
3. A user submits an inbound transaction that ultimately produces an `INBOUND_REVERT` outbound; `buildRevertOutbound` fetches `GasFee`/`GasPrice`/`GasLimit` from the stale on-chain value via `GetGasFeeInfoForRevertOutbound` → `GetOutboundTxGasAndFees`.
4. Once the outbound observation reports the real `GasFeeUsed` (based on actual, moved gas price), `applyGasRefund` computes `refundAmount = GasFee(stale) - GasFeeUsed(real)`, and if the stale value is inflated, the user is refunded more PRC20/native gas token than the true cost — with no code path ever having checked whether the price used was fresh. [8](#0-7)

### Citations

**File:** x/uexecutor/keeper/evm.go (L305-347)
```go
// Calls UniversalCore Contract to set chain metadata (gas price + chain height).
// The contract uses block.timestamp for the observed-at value.
func (k Keeper) CallUniversalCoreSetChainMeta(
	ctx sdk.Context,
	chainNamespace string,
	price *big.Int,
	chainHeight *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"setChainMeta",
		chainNamespace,
		price,
		chainHeight,
	)
}
```

**File:** x/uexecutor/types/abi.go (L362-368)
```go
    {
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

**File:** universalClient/chains/evm/chain_meta_oracle.go (L83-102)
```go
		case <-ticker.C:
			// Fetch current gas price
			gasPrice, err := g.rpcClient.GetGasPrice(ctx)
			if err != nil {
				g.logger.Error().Err(err).Msg("failed to fetch gas price")
				continue
			}

			// Log the gas price
			g.logger.Debug().
				Str("chain", g.chainID).
				Str("gas_price", gasPrice.String()).
				Msg("fetched gas price")

			// Get current block number
			blockNumber, err := g.rpcClient.GetLatestBlock(ctx)
			if err != nil {
				g.logger.Error().Err(err).Msg("failed to get latest block number")
				continue
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
