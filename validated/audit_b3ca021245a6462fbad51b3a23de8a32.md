### Title
Non-TWAP, snapshot-based `ChainMeta` gas-price oracle lets an unprivileged attacker inflate the on-chain gas price for a whole staleness window, causing all subsequent outbound withdrawals to overpay `gasFee` - (File: `x/uexecutor/keeper/chain_meta.go`, `x/uexecutor/keeper/gas_fee.go`)

### Summary
Push Chain's `ChainMeta` oracle (the destination-chain gas-price feed consumed by `getOutboundTxGasAndFees`/`refundUnusedGas`) is, like the Curve-metapool oracle in the referenced report, not a genuine time-weighted average — it is the median of each Universal Validator's *most recent single RPC read* of the external chain's instantaneous gas price, gated only by a fixed 300-second staleness window (`chainMetaVoteStalenessSeconds`). An unprivileged actor who can spike the real gas price on an external chain for a short burst (self-spam with high-priority-fee transactions, which anyone can permissionlessly do) causes every honest, honestly-reporting validator to independently observe and vote the inflated value. Because each validator's vote simply overwrites its own prior entry (`x/uexecutor/keeper/chain_meta.go` lines 92-108) and the median is recomputed on every vote, the inflated median propagates to the on-chain `UniversalCore.gasPriceByChainNamespace` value and stays there — poisoning the fee charged to *every* user who creates an outbound during the staleness window, not just the attacker.

### Finding Description
`VoteChainMeta` (`x/uexecutor/keeper/chain_meta.go:62-186`) stores one `(price, chainHeight, storedAt)` triple per validator and computes `medianPrice`/`medianChainHeight` as the upper median over all votes whose `storedAt` is within `chainMetaVoteStalenessSeconds` of "now" [1](#0-0) . This median is written to the `UniversalCore` contract via `CallUniversalCoreSetChainMeta`, and read back by `GetGasPriceByChain`/`GetOutboundTxGasAndFees` at the moment a user's outbound (withdrawal) is created [2](#0-1) . The resulting `gasFee`/`gasPrice` are baked into the `OutboundTx` (and are what gets debited from the user in the gas-token PRC20) [3](#0-2) .

Unlike a real TWAP, there is no time-integration of price over the interval — each validator simply reports "the gas price I saw right now," and the staleness window only decides which *snapshot* votes are eligible, not how long a manipulated snapshot's *effect* persists. The per-validator "latest wins" and "median of currently-fresh votes" mechanism means:
1. An attacker temporarily drives up the real gas price on the external chain (e.g., a short burst of self-submitted high-priority-fee transactions — something any unprivileged user can do on a public chain).
2. Every `ChainMetaOracle` on every Universal Validator observes this real (not falsified) spike via RPC (`universalClient/chains/evm/chain_meta_oracle.go:83-96`) and honestly votes it in.
3. The recomputed median becomes elevated and is written to `UniversalCore.gasPriceByChainNamespace`.
4. The inflated price persists as the *last applied* value until a fresher, lower set of votes both arrives **and** clears the `chainMetaMinVotesForFirstWrite`/staleness gates — for up to `chainMetaVoteStalenessSeconds` (300s) per validator's vote cadence, and in practice longer since the interval between validator polls (default 30s, configurable) plus the staleness window can stack.
5. Every *other, unrelated* user who creates an outbound during that window has their `gasFee`/`gasPrice` computed against the inflated snapshot, overpaying in gas-token PRC20, exactly mirroring the report's core complaint: "temporarily manipulate a supposedly time-weighted aggregate and the wrong value persists for a window that outlives the manipulation, corrupting an unrelated party's transaction."

This is the same conceptual root cause identified in the external report ("computes an instant value based on the current snapshot rather than a genuine time-integrated average, making the derived on-chain price manipulable by an actor who can temporarily influence the underlying inputs") mapped onto Push Chain's own gas-price/chain-meta subsystem, which explicitly feeds fund-accounting (`gasFee` debited from users, `refundUnusedGas` reconciliation) rather than an AMM balance ratio.

### Impact Explanation
This falls under "corruption of ... gas fee accounting ... token mapping ... or canonical UniversalTx state" from the allowed-impact list. Concretely: unrelated, honest users creating outbounds during the poisoned window are overcharged `gasFee` in the PRC20 gas token relative to the true, non-manipulated market price, because the on-chain `gasPriceByChainNamespace` value they see at withdrawal time is the byproduct of a short-lived, attacker-timed spike rather than a genuine time-weighted signal. Because refunds (`applyGasRefund`, `x/uexecutor/keeper/outbound.go:174-257`) only return the delta between the (inflated) locked-in `gasFee` and the *actual* `gasFeeUsed` reported later by validator observation, users still pay the extra cost through slippage/fees on the swap-based refund path (`getSwapQuoteForRefund` with a 5% `minPCOut` tolerance) and through the round-trip cost of the refund itself, rather than never having been overcharged in the first place. This is a fund-impacting accounting corruption reachable by any unprivileged actor without needing to compromise validators, relayers, or governance — it only relies on honest validators faithfully reporting a real (attacker-timed) market condition into a mechanism that treats a point-in-time snapshot as if it were a stable, averaged reference price.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: it requires the attacker to sustain a real gas-price spike on the destination chain across enough validator polling cycles to shift the median (validators poll independently, default every 30s, and need `chainMetaMinVotesForFirstWrite` fresh votes to move a bootstrapped oracle), and the financial upside per victim transaction is bounded by the price delta times each victim's `gasLimit`. It is nonetheless a fully unprivileged, permissionless attack requiring no validator, relayer, or admin compromise — only the ability to submit transactions on a public external chain, which is the same low bar as the original TWAP-manipulation report.

### Recommendation
Replace the "median of latest single-snapshot votes within a staleness window" model with a genuine time-weighted or multi-sample aggregation (e.g., require each validator to submit a rolling average of several recent block/gas observations rather than one instantaneous reading, and/or widen the aggregation window and apply outlier/deviation clamps against the previously applied value) so a short-lived, attacker-timed spike cannot single-handedly move the on-chain gas price used to charge unrelated users' `gasFee`. Consider also capping the maximum per-update deviation from `LastAppliedChainHeight`'s associated price (already partially done for chain height monotonicity but not for price bounds) to dampen sudden swings.

### Proof of Concept
1. Attacker identifies an external chain `C` registered in `uregistry` with active inbound/outbound flows.
2. Attacker submits a short burst of self-transactions on `C` with abnormally high priority fees, causing the chain's real-time gas price to spike for the duration of a few blocks.
3. Each Universal Validator's `ChainMetaOracle.fetchAndVoteChainMeta` (`universalClient/chains/evm/chain_meta_oracle.go:83-119`) independently polls `C`'s RPC during the spike and honestly votes the inflated price via `VoteChainMeta`.
4. `x/uexecutor/keeper/chain_meta.go`'s `VoteChainMeta` recomputes the upper median (`upperMedianUint64`) over currently-fresh votes and, once ≥`chainMetaMinVotesForFirstWrite` or already bootstrapped, calls `CallUniversalCoreSetChainMeta`, writing the inflated median to `UniversalCore.gasPriceByChainNamespace`.
5. Any other, uninvolved user submits a withdrawal on Push Chain targeting chain `C` during the window before the median reverts (bounded by validator poll interval + `chainMetaVoteStalenessSeconds`); `GetOutboundTxGasAndFees` (`x/uexecutor/keeper/gas_fee.go:26-64`) returns the inflated `gasFee`/`gasPrice`, which is debited from that user's PRC20 gas token balance at outbound creation (`x/uexecutor/keeper/create_outbound.go:69-91`).
6. Once the attacker's spam stops and the real gas price reverts, the victim's actual `gasFeeUsed` (observed later by UVs) is much lower than the locked-in `gasFee`, and `applyGasRefund` triggers a swap-based refund subject to slippage/fees — the victim never fully recovers the manipulated overcharge, and the price of `C` returns to normal in time for the attacker's own transactions to proceed unaffected.

### Citations

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

**File:** x/uexecutor/keeper/gas_fee.go (L26-64)
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
