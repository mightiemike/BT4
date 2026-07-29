## Analysis: Analog Found (with caveats on unverifiable funding mechanics)

### Title
Outbound gas fee/price snapshotted at UTX-creation time is never re-validated against destination-chain volatility, and shortfalls are silently unhandled - (File: `x/uexecutor/keeper/create_outbound.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/gas_fee.go`)

### Summary
The Omni report's core defect — a data/gas cost computed once, using a currently-observed gas price/exchange rate, with no premium for the delay before actual execution — has a structural analog in Push Chain's outbound gas accounting. `outbound.GasFee`, `outbound.GasPrice`, and `outbound.GasLimit` are captured once at outbound-creation time from the `UniversalTxOutbound` event emitted by the `UniversalGatewayPC` contract [1](#0-0) , which in turn derives from `UniversalCore.getOutboundTxGasAndFees()` reading the oracle's currently-stored `gasPrice`/`baseGasLimitByChainNamespace` [2](#0-1) . That oracle price is itself a median of validator-submitted `ChainMeta` votes updated only periodically [3](#0-2) . There is an unavoidable delay between this snapshot and the moment TSS actually signs and broadcasts the outbound transaction on the destination chain, exactly mirroring the report's core observation that `dataGasPrice`/exchange-rate volatility between `xcall()` and `xsubmit()` is not compensated for.

### Finding Description
When an outbound is created, `outbound.GasFee` is a fixed amount computed from the gas price known to the chain at that instant [4](#0-3) . After the outbound is later observed as executed, `applyGasRefund` only handles the case where the allocated `GasFee` exceeds the actually-used `GasFeeUsed` (refunding the excess) [5](#0-4) . There is no symmetric path in `applyGasRefund` (nor anywhere else found in `handleFailedOutbound`/`handleSuccessfulOutbound`) that tops up or otherwise reconciles the case where destination-chain gas price rose between outbound creation and broadcast such that `GasFeeUsed > GasFee`. Like `FeeOracleV1.feeFor()`, the amount funded for gas at commit time is not defended with any overhead/premium against volatility in the interval before the transaction is actually mined on the destination chain.

### Impact Explanation
If destination-chain gas prices rise materially between outbound creation (when `GasFee` is locked in from the oracle-median snapshot) and TSS broadcast, the amount reserved/swapped for gas may be insufficient to cover the real cost of the destination-chain transaction. Depending on how the TSS transaction-builder funds itself (this codebase snippet — the exact wiring between `outbound.GasFee`/`GasPrice` and `universalClient/chains/*/tx_builder.go` broadcast logic — was not fully traceable within the available search budget), this could result in either (a) a purely protocol-absorbed loss (out of scope, not a user-fund issue), or (b) an outbound that cannot be successfully broadcast/completed because the funded gas allowance is insufficient, which would leave the underlying bridged/withdrawn funds stuck in `PENDING`/failed-to-broadcast state — a potential "permanent freezing" of user funds reachable purely through ordinary use (no privileged actor, no malicious relayer needed), since the volatility window exists for every honest outbound.

### Likelihood Explanation
Likelihood is **uncertain and not confirmed** with the tools available in this session. I was not able to fully trace whether:
1. The TSS wallet/broadcaster on the destination chain draws gas strictly from the `GasFee` amount computed at outbound-creation time, or re-queries live gas price at broadcast time from its own RPC (in which case underfunding is a protocol-economics issue, not a fund-freezing issue for the user).
2. Whether any downstream retry/rebroadcast/top-up logic exists elsewhere in `universalClient/` that compensates for a stale allocation.

Given the project's own acknowledgment pattern seen elsewhere in the code (careful excess-refund handling, explicit swap-fallback paths in `applyGasRefund`), it is plausible the team already handles this via off-chain TSS logic not indexed here. Without confirming the destination-chain broadcast funding source, this cannot be asserted as a confirmed, reachable, user-triggerable freeze with the same confidence as the other invariant classes in scope.

### Recommendation
- Verify whether `universalClient` TSS broadcasters fund destination-chain outbound gas strictly from the on-chain `GasFee` snapshot or from a live-queried price; if the former, add a volatility premium (analogous to the report's recommendation) to `GetOutboundTxGasAndFees`, and add a symmetric "top-up" or safe-abort/retry path for outbounds where `GasFeeUsed > GasFee`, so no outbound can become permanently stuck due to an underfunded gas allocation.
- If the shortfall is purely a protocol PnL matter (TSS/module wallet absorbs it, no user funds ever get stuck), this reduces to an economic design tradeoff, not a security-scoped finding.

### Proof of Concept
Not constructed — a concrete PoC requires confirming the destination-chain broadcast funding mechanism in `universalClient/chains/*/tx_builder.go`, which could not be fully verified within the remaining tool budget. This finding is submitted with explicit uncertainty rather than being asserted as a confirmed vulnerability.

### Citations

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

**File:** x/uexecutor/keeper/gas_price.go (L15-49)
```go
// PruneValidatorVotes removes a validator's votes from all ChainMetas entries.
// Called when a validator is removed from the universal validator set to prevent stale votes
// from influencing the median calculations.
func (k Keeper) PruneValidatorVotes(ctx context.Context, validatorAddr string) {
	k.Logger().Debug("pruning validator votes from chain metas", "validator", validatorAddr)

	_ = k.ChainMetas.Walk(ctx, nil, func(chainId string, entry types.ChainMeta) (bool, error) {
		idx := -1
		for i, s := range entry.Signers {
			if s == validatorAddr {
				idx = i
				break
			}
		}
		if idx >= 0 && len(entry.Signers) > 1 {
			entry.Signers = append(entry.Signers[:idx], entry.Signers[idx+1:]...)
			entry.Prices = append(entry.Prices[:idx], entry.Prices[idx+1:]...)
			entry.ChainHeights = append(entry.ChainHeights[:idx], entry.ChainHeights[idx+1:]...)
			entry.StoredAts = append(entry.StoredAts[:idx], entry.StoredAts[idx+1:]...)
			entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))
			_ = k.ChainMetas.Set(ctx, chainId, entry)
			k.Logger().Debug("pruned validator vote from chain meta",
				"validator", validatorAddr,
				"chain_id", chainId,
				"remaining_signers", len(entry.Signers),
			)
		} else if idx >= 0 && len(entry.Signers) == 1 {
			_ = k.ChainMetas.Remove(ctx, chainId)
			k.Logger().Debug("removed chain meta entry after last signer pruned",
				"validator", validatorAddr,
				"chain_id", chainId,
			)
		}
		return false, nil
	})
```

**File:** x/uexecutor/keeper/outbound.go (L174-197)
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

```
