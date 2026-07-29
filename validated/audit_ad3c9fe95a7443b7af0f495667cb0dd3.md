Confirmed: `buildRevertOutbound` produces a proper `INBOUND_REVERT` outbound that returns the bridged amount to the sender on the source chain, so swap failures on the `GAS`/`GAS_AND_PAYLOAD` inbound routes are not permanently frozen — they fall back to a refund path. This differs materially from the Sturdy `LidoVault` scenario where withdrawal had no fallback at all.

### Title
Hardcoded 5% slippage on module-driven Uniswap V3 swaps allows sandwich extraction of user gas-conversion value - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The `uexecutor` module performs three module-originated Uniswap V3 swaps — inbound gas top-up (`ExecuteInboundGas`), inbound gas+payload top-up (`gasAndPayloadDepositAutoSwap`), and outbound gas-refund (`applyGasRefund`) — all of which compute `minPCOut` as a hardcoded `quote * 95 / 100` immediately after fetching a live `QuoterV2` quote, then execute the swap in the same call. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
Since the "get quote" and "execute swap" calls both read the pool's live on-chain reserves and are only separated by the module's own subsequent EVM call within the same block/transaction ordering, an unprivileged attacker can front-run the block/transaction that triggers this module-originated swap (e.g., by submitting a large trade against the same Uniswap V3 pool used by `GetSwapQuote`/`CallPRC20DepositAutoSwap`) to move the pool price unfavorably just before the module fetches its quote, then back-run it afterward to restore the price and capture the difference. Because `minPCOut` is derived from the *already-manipulated* quote with a fixed 95% tolerance rather than a price bound anchored to a trusted reference or a user/operator-configurable tolerance, the protocol's own slippage guard offers no protection against this — it simply re-validates against the manipulated price, permitting the swap to execute at an attacker-favorable rate. This is the same "hardcoded slippage constant" bug class as the external report's `GeneralVault`/`LidoVault` 99%/1% tolerance, but manifests here as sandwich-extractable value loss on the PRC20→WPC conversion rather than outright freezing, since — unlike the LIDO analog — failed swaps on the inbound gas routes do fall back to a proper `INBOUND_REVERT` outbound via `buildRevertOutbound`. [4](#0-3) 

For the gas-refund path (`applyGasRefund`), a failed/slipped swap does fall back to a no-swap direct PRC20 deposit, so funds are not lost there either — but the swap-with-slippage leg itself, when it *does* succeed, can still execute against a manipulated price since the 95% floor is computed from the same manipulated quote. [5](#0-4) 

### Impact Explanation
An unprivileged attacker who can predict or observe pending inbound-gas/inbound-gas-and-payload votes (or gas-refund observations) about to be finalized can sandwich the module's Uniswap V3 swap to skim value from the amount of native PC delivered to the user's UEA or to the refund recipient. This is a "corruption of ... gas fee accounting / refund accounting" and value-extraction impact against user-controlled funds reachable purely from ordinary inbound/outbound flows, without any privileged role. The percentage is fixed at 5% for all three call sites regardless of token, pool depth, or market conditions, so thinly-liquid PRC20/WPC pools are especially exposed.

### Likelihood Explanation
Likelihood is moderate: it requires the attacker to have a tradable position against the specific Uniswap V3 pool used for the given PRC20 and to time a transaction immediately around the block executing the module's swap call. Given that inbound/outbound finalization is driven by validator votes reaching quorum (a somewhat predictable, non-secret event) and the pools in question are standard on-chain AMMs, this is a realistic MEV/sandwich scenario rather than a purely theoretical one.

### Recommendation
Do not derive the slippage floor purely from a quote fetched by the module in the same transaction as the swap. Instead, anchor `minPCOut` to a manipulation-resistant reference (e.g., a TWAP over multiple blocks) and/or make the tolerance configurable via governance/module params instead of a hardcoded `95`/`100` constant baked into three separate call sites, consistent with the report's recommendation to avoid a single fixed tolerance that cannot react to abnormal pool conditions.

### Proof of Concept
1. Attacker identifies a pending `MsgVoteInbound`/`MsgVoteOutbound` set that will reach quorum and trigger `ExecuteInboundGas` (or the GAS_AND_PAYLOAD/refund equivalents) for a PRC20 with a thinly-liquid Uniswap V3 pool.
2. Attacker submits a large swap against that same pool immediately before the finalizing transaction is processed, moving the pool price against the pending conversion direction.
3. The module calls `GetSwapQuote` [6](#0-5)  against the now-manipulated pool state, computes `minPCOut = quote * 95 / 100` [7](#0-6) , and executes `CallPRC20DepositAutoSwap` against the same manipulated pool — passing the 95% floor with no reference to a fair/undisturbed price.
4. Attacker back-runs with an opposing trade to restore the pool price and realize the arbitrage profit, extracted at the expense of the amount of native PC actually credited to the victim's UEA (or refund recipient).

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L192-208)
```go
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}
```

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
}
```
