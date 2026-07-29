Based on the analysis, this confirms `x/uexecutor/keeper/chain_meta.go` uses a single global constant `chainMetaVoteStalenessSeconds = 300` for the freshness gate on every chain's gas-price/chain-height votes, and this value is never derived from the per-chain `ChainConfig.GasOracleFetchInterval` already stored in `x/uregistry`, even though that field exists specifically to describe how often each chain's price should be refreshed.

### Title
Chain-agnostic hardcoded oracle staleness window in `VoteChainMeta` ignores per-chain gas-price cadence, permitting outdated gas-price data into fee accounting - (File: x/uexecutor/keeper/chain_meta.go)

### Summary
`x/uexecutor/keeper/chain_meta.go` gates which Universal Validator votes are "fresh" enough to be folded into the on-chain gas-price/chain-height median using a single hardcoded constant, `chainMetaVoteStalenessSeconds = 300` seconds, applied identically to every external chain [1](#0-0) . This mirrors the reported `ChainlinkPriceOracle` bug class: a single freshness/heartbeat interval reused across feeds/assets that actually have different, chain-specific update cadences.

### Finding Description
`x/uregistry`'s `ChainConfig` already models a per-chain cadence for exactly this purpose: `gas_oracle_fetch_interval` ("how often relayers should fetch gas prices") [2](#0-1) , and each chain's config (e.g. BSC testnet `30s`, others may differ) declares its own value [3](#0-2) . The `universalClient` chain-meta oracles for EVM and SVM also read a per-chain `gas_price_interval_seconds` to decide how often to poll and vote [4](#0-3) .

However, the consensus-side freshness filter in `VoteChainMeta` never reads any of this per-chain configuration. It unconditionally treats any vote stamped within the last 300 seconds as "fresh" and includes it in the price/height median computation, for every chain regardless of that chain's actual polling cadence or its real-world price volatility [5](#0-4) . The computed median price is then written directly into the `UniversalCore` EVM contract via `CallUniversalCoreSetChainMeta` [6](#0-5) , and that stored price subsequently drives `getOutboundTxGasAndFees`, which computes the `gasFee`/`gasPrice` charged in PRC20 tokens for outbound relay execution on the destination chain [7](#0-6) .

Because the staleness window is decoupled from each chain's actual price cadence, a chain whose gas price is volatile on a timescale shorter than 300 seconds (congestion spikes, EIP-1559 base-fee jumps, L2 sequencer fee changes) can have its on-chain gas price median built from a mix of samples up to 5 minutes stale, well after prices have moved materially from what any individual validator most recently observed.

### Impact Explanation
Any ordinary user submitting a crosschain payload/inbound during a window where destination-chain gas prices are moving faster than the fixed 300-second staleness gate reflects will have their outbound gas fee computed from stale/smoothed gas-price data via `GetOutboundTxGasAndFees` / `getOutboundTxGasAndFees` [8](#0-7) . This falls under corruption of gas fee accounting: users can be systematically over- or under-charged in PRC20 relative to actual destination-chain execution cost, and under-funded outbound transactions risk failing to execute on the destination chain, stalling in `BROADCASTED` state or forcing revert/refund handling — with the protocol (rather than the user) absorbing the shortfall if the relayer must still complete or pay to unwind the transaction.

### Likelihood Explanation
No malicious actor or privileged role is required — this triggers purely from an ordinary user submitting a normal crosschain transaction during a period of legitimate destination-chain gas-price volatility, which is routine and expected behavior for any of the supported EVM/SVM chains. The single global constant guarantees the mismatch will occur on any chain whose real fetch/volatility cadence differs materially from 300 seconds, which is virtually all of them since the registry's own per-chain `gas_oracle_fetch_interval` values are typically much shorter (e.g. `30s`).

### Recommendation
Derive the staleness window used in `VoteChainMeta` from the per-chain `ChainConfig.GasOracleFetchInterval` (or an equivalent registry-sourced value) instead of the fixed `chainMetaVoteStalenessSeconds` constant, so each chain's fresh-vote gate reflects that chain's actual expected update cadence, analogous to using each token/feed's real heartbeat in `ChainlinkPriceOracle`.

### Proof of Concept
1. Register two chains in `uregistry` with very different `gas_oracle_fetch_interval` values (e.g. 5s vs. 250s).
2. Have Universal Validators vote gas prices for both chains at the same cadence pattern; for the fast chain, inject a price spike in the last vote while older (up to 300s) low-price votes are still counted as "fresh" per `chainMetaVoteStalenessSeconds` [9](#0-8) .
3. Observe that `VoteChainMeta`'s computed median still blends the outdated low-price votes into the applied `UniversalCore` gas price, as demonstrated by the existing staleness test that only excludes votes older than 300s regardless of chain [10](#0-9) .
4. Submit a crosschain payload targeting the fast-moving chain and confirm `GetOutboundTxGasAndFees` returns a `gasFee` computed from the stale blended price rather than the chain's true current price.

### Citations

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

**File:** x/uexecutor/keeper/chain_meta.go (L171-175)
```go
	priceBig := math.NewUint(medianPrice).BigInt()
	chainHeightBig := math.NewUint(medianChainHeight).BigInt()
	if _, evmErr := k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig); evmErr != nil {
		return sdkerrors.Wrap(evmErr, "failed to call EVM setChainMeta")
	}
```

**File:** proto/uregistry/v1/types.proto (L114-114)
```text
  google.protobuf.Duration gas_oracle_fetch_interval = 8 [(gogoproto.nullable) = false, (gogoproto.stdduration) = true]; // how often relayers should fetch gas prices
```

**File:** config/testnet-donut/bsc_testnet/chain.json (L10-10)
```json
  "gas_oracle_fetch_interval": "30s",
```

**File:** universalClient/chains/evm/chain_meta_oracle.go (L138-145)
```go
// getChainMetaOracleFetchInterval returns the gas oracle fetch interval
func (g *ChainMetaOracle) getChainMetaOracleFetchInterval() time.Duration {
	if g.gasPriceIntervalSeconds <= 0 {
		return 30 * time.Second
	}

	return time.Duration(g.gasPriceIntervalSeconds) * time.Second
}
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-24)
```go
// GetOutboundTxGasAndFees calls UniversalCore.getOutboundTxGasAndFees(prc20, gasLimitWithBaseLimit)
// to get gasToken, gasFee, protocolFee, gasPrice, and chainNamespace.
```

**File:** x/uexecutor/keeper/gas_fee.go (L26-63)
```go
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

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L239-244)
```go
		// Advance block time by 301 seconds — old votes become stale.
		ctx = ctx.WithBlockTime(ctx.BlockTime().Add(301 * time.Second))

		// val0 re-votes with price=900, height=3 (> lastApplied=2).
		// Only this fresh vote contributes to the new median.
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 900, 3))
```
