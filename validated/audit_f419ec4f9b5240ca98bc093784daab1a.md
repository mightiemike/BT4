## Analysis

The external report is about Chainlink price consumers failing to check feed freshness (L2 sequencer uptime / `updatedAt`), leading to use of stale-but-plausible-looking prices. I searched Push Chain's `uexecutor` gas/chain-meta oracle path for the same class of bug.

Push Chain's chain-meta *write* path (`x/uexecutor/keeper/chain_meta.go`, `VoteChainMeta`) is heavily staleness-guarded: it filters votes older than `chainMetaVoteStalenessSeconds` (300s) before computing the median, enforces monotonic `LastAppliedChainHeight`, and requires a bootstrap quorum. [1](#0-0) [2](#0-1) 

However, the on-chain `UniversalCore` contract also exposes `timestampObservedAtByChainNamespace`, which records when the price/height were last written. [3](#0-2) 

I found no Go caller anywhere in the repo that reads or checks this getter. Every consumer of the stored gas price/fee data — `GetGasPriceByChain`, `GetOutboundTxGasAndFees`, `GetL1GasFeeByChain`, `GetTssFundMigrationGasLimitByChain` — calls the contract's view function and uses the returned value unconditionally, with no freshness check against `timestampObservedAtByChainNamespace` or any local staleness window at the *read* site: [4](#0-3) [5](#0-4) 

These values feed directly into fund-affecting flows:
- `buildRevertOutbound` populates `GasFee`/`GasPrice`/`GasLimit` on revert outbounds from this unchecked read. [6](#0-5) 
- `applyGasRefund` compares the (possibly stale) `outbound.GasFee` against the UV-observed `gasFeeUsed` to decide refund amounts, and calls `CallUniversalCoreRefundUnusedGas` to move real PRC20/PC value. [7](#0-6) [8](#0-7) 
- `InitiateFundMigration` uses `GetGasPriceByChain`/`GetL1GasFeeByChain` unchecked to size the TSS fund-migration sweep transfer. [9](#0-8) 

### Title
Gas-price/chain-meta oracle reads have no staleness check, allowing stale prices to corrupt outbound fee accounting and fund-migration sizing - (File: `x/uexecutor/keeper/gas_fee.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`VoteChainMeta` carefully filters stale validator votes before writing a median price to `UniversalCore.setChainMeta`, and the contract records an observed-at timestamp (`timestampObservedAtByChainNamespace`). But every consumer of that stored price (`GetGasPriceByChain`, `GetOutboundTxGasAndFees`, `GetL1GasFeeByChain`, fund-migration gas sizing) reads the value with no freshness check at all — the write-side staleness protection is never enforced on the read side that actually spends funds.

### Finding Description
If a chain's universal validators stop voting fresh chain-meta updates for that `chainNamespace` (RPC outage against that specific external chain, low-traffic/low-priority chain, temporary UV downtime, etc.), `ChainMetas[chainNamespace]` and the mirrored `UniversalCore` contract storage simply retain the last-applied price/height indefinitely — there is no on-chain or off-chain guard that treats an old `timestampObservedAtByChainNamespace` as invalid, in contrast to Chainlink's recommended sequencer/heartbeat check. `GetGasPriceByChain`, `GetOutboundTxGasAndFees`, and `GetTssFundMigrationGasLimitByChain`/`GetL1GasFeeByChain` all call the view functions and use `results[...]` directly with no age check.

### Impact Explanation
Ordinary users creating outbound/refund flows on a chain whose gas oracle has gone stale will have their `OutboundTx.GasFee`/`GasPrice` populated from out-of-date data. If the real destination-chain gas price has since risen materially, the protocol under-collects the fee it charges the user relative to what it actually pays out on the destination chain via the relayer/TSS execution, causing protocol-controlled fund loss over time. The same stale value sizes the TSS fund-migration sweep transaction, risking an incorrect (insufficient) migration transfer relative to actual on-chain gas cost.

### Likelihood Explanation
Requires only that a chain's chain-meta stop being refreshed (a plausible, non-adversarial, non-privileged condition — a low-traffic chain, temporary RPC issues for one external chain, etc.) combined with genuine price movement on that external chain during the stale window; no compromise of validators, TSS, or governance is needed, and it is reachable purely from ordinary user-submitted outbound-generating transactions.

### Recommendation
Read and enforce `timestampObservedAtByChainNamespace` (or an equivalent freshness field) wherever `gasPriceByChainNamespace`/`getOutboundTxGasAndFees`/`l1GasFeeByChainNamespace` are consumed for fee or migration-amount calculations, rejecting or falling back safely when the observed-at age exceeds a defined staleness threshold — mirroring the `chainMetaVoteStalenessSeconds` guard already used on the write side.

### Proof of Concept
1. Let chain `eip155:X` bootstrap its `ChainMeta` normally, then stop all UV votes for it (e.g., simulate a stalled `ChainMetaOracle.fetchAndVoteChainMeta` loop for that chain).
2. `UniversalCore.gasPriceByChainNamespace("eip155:X")` and `timestampObservedAtByChainNamespace("eip155:X")` continue returning the old price with an increasingly stale timestamp — confirmed by `TestVoteChainMetaContractState`/`chain_meta.go` behavior showing no expiry is enforced on the stored value itself.
3. Trigger a user outbound for `eip155:X`; `buildRevertOutbound`/`GetOutboundTxGasAndFees` populate `GasFee`/`GasPrice` from the stale read with no age check, as shown in `x/uexecutor/keeper/gas_fee.go` and `x/uexecutor/keeper/build_revert_outbound.go`.
4. If real gas price on `eip155:X` has risen since the last vote, the fee charged to the user/protocol is based on the outdated (lower) price, under-collecting relative to actual execution cost.

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L16-29)
```go
const (
	// chainMetaVoteStalenessSeconds is the maximum age (in seconds) of a stored vote
	// that is still eligible to be included in the median calculation.
	chainMetaVoteStalenessSeconds uint64 = 300

	// chainMetaMinVotesForFirstWrite is the number of fresh votes required
	// before the first EVM oracle write happens for a given observed chain.
	// This prevents a single validator (or a single outlier) from defining
	// the oracle's initial values. With 3 votes, the upper median (index
	// len/2 = 1) is the middle value, which is robust against a single
	// outlier on either side. After bootstrap (LastAppliedChainHeight > 0),
	// the normal median-on-each-fresh-vote behaviour applies.
	chainMetaMinVotesForFirstWrite int = 3
)
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

**File:** x/uexecutor/keeper/outbound.go (L174-198)
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
```

**File:** x/uexecutor/keeper/outbound.go (L213-230)
```go
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

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L65-83)
```go
	// 7. Fetch gas price, fund-migration gas limit, and L1 gas fee from UniversalCore.
	gasPrice, err := k.uexecutorKeeper.GetGasPriceByChain(sdkCtx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to get gas price for chain %s: %w", chain, err)
	}

	gasLimitBig, err := k.uexecutorKeeper.GetTssFundMigrationGasLimitByChain(sdkCtx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to get tss fund migration gas limit for chain %s: %w", chain, err)
	}
	if gasLimitBig == nil || !gasLimitBig.IsUint64() || gasLimitBig.Uint64() == 0 {
		return 0, fmt.Errorf("invalid tss fund migration gas limit for chain %s: %s", chain, gasLimitBig)
	}
	gasLimit := gasLimitBig.Uint64()

	l1GasFee, err := k.uexecutorKeeper.GetL1GasFeeByChain(sdkCtx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to get l1 gas fee for chain %s: %w", chain, err)
	}
```
