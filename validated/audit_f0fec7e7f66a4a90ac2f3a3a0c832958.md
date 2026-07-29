I have enough context to produce the analog finding now.

### Title
Instantaneous Uniswap V3 spot-price quote allows sandwich manipulation of PRC20→PC autoswap during inbound execution and gas refund - ([File: x/uexecutor/keeper/evm.go])

### Summary
The `GetSwapQuote`/`CallPRC20DepositAutoSwap` flow used by `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` (and the mirrored gas-refund path in `applyGasRefund`) computes the slippage bound `minPCOut` directly from a same-block instantaneous Uniswap V3 `QuoterV2.quoteExactInputSingle` read of the WPC/PRC20 pool, then immediately executes the swap against that same pool in the same validator-driven EVM call. Because the "protective" bound is derived from the very price an attacker can move beforehand (no TWAP, no historical/epoch price bound, no independent oracle), an unprivileged user can sandwich the auto-swap exactly like the reported Pyth/LPManager same-block price manipulation, extracting value from the WPC pool that backs deposits and gas refunds.

### Finding Description
For `GAS` and `GAS_AND_PAYLOAD` inbound routes, once a Universal Validator quorum finalizes an inbound, the keeper:
1. Fetches a spot quote via `GetSwapQuote` → `QuoterV2.quoteExactInputSingle`, which simulates the swap against the pool's *current* tick/sqrtPriceX96 [1](#0-0) .
2. Derives `minPCOut = quote * 95 / 100` — a 5% slippage tolerance computed from that same instantaneous quote [2](#0-1) .
3. Immediately executes `depositPRC20WithAutoSwap` against the same pool with that `minPCOut` as the only protection [3](#0-2) .

The identical pattern is reused for `GAS_AND_PAYLOAD` inbound execution [4](#0-3)  and for the excess-gas refund swap on outbound finalization [5](#0-4) .

This is structurally the same flaw as the reported LPManager issue: a value-bearing conversion (shares in LPManager; PC output here) is priced from a live, atomically-updatable oracle/AMM value, and the "protection" (5% slippage bound) is computed from that same manipulable value rather than from a time-weighted or epoch-bounded reference. Because the quote and the execution happen inside the same Push Chain block/transaction sequence (the inbound-finalizing tx that triggers `ExecuteInboundGas`), an attacker who moves the WPC/PRC20 pool price with a prior transaction in the same block can force the auto-swap to execute at the manipulated price, extracting value from the pool. The e2e test suite explicitly acknowledges pool liquidity is thin ("Surfpool fees are negligible... Keep local Solana gas quotes small so repeated Route 3 tests do not drain the tiny WPC/pSOL AMM pool"), underscoring how cheap this manipulation is in practice [6](#0-5) .

By contrast, the `ChainMeta` gas-price oracle in the same module already implements the exact epoch/median/staleness mitigation the external report recommends — multiple validator votes, an upper-median aggregation, and a staleness window before any value is applied on-chain [7](#0-6) . The swap-quote path has no equivalent defense: it is a single, atomic, spot-price read with no aggregation across time or validators.

### Impact Explanation
An attacker can drain value from the WPC/PRC20 liquidity pool used to back deposits and gas refunds by sandwiching the auto-swap: push the pool price in a favorable direction just before the inbound/outbound-finalizing transaction executes, let the manipulated quote produce a `minPCOut` that still clears (since it's 95% of the manipulated price, not of a fair price), then reverse the price move afterward to capture the difference. This directly corrupts PRC20/native accounting and results in unauthorized value extraction from protocol/pool-controlled funds, which is explicitly in scope ("stealing, draining ... of user or protocol-controlled funds").

### Likelihood Explanation
The attack requires only an ordinary unprivileged user able to submit EVM transactions on Push Chain to move the pool price and does not require any validator, relayer, or admin privilege — the swap execution itself is triggered deterministically by inbound quorum finalization, which is a normal, expected, attacker-triggerable event (the attacker controls the deposit/inbound that causes the swap). Given the explicitly small pool sizes noted in the test infrastructure, the capital required to move price meaningfully is low, making this highly likely to be economically viable once real AMM pools are live.

### Recommendation
Do not derive the slippage bound from the same instantaneous quote that is about to be executed against. Use a Uniswap V3 TWAP (time-weighted average price observation window) or an epoch-based min/max price tracked over the last `N` blocks/seconds (mirroring the `ChainMeta` median/staleness pattern already used elsewhere in this module) to compute `minPCOut`, and reject swaps whose current spot price deviates beyond a bounded threshold from that reference. Apply the same fix to the gas-refund swap path in `applyGasRefund`/`getSwapQuoteForRefund`.

### Proof of Concept
1. Attacker identifies a low-liquidity WPC/PRC20 (e.g., WPC/pSOL) pool used by `depositPRC20WithAutoSwap`.
2. Attacker submits a large swap on the pool in a preceding transaction within the same block, moving the spot price in their favor.
3. Attacker (or an accomplice) triggers/completes a `GAS`/`GAS_AND_PAYLOAD` inbound whose quorum finalizes in the same block, causing `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` to call `GetSwapQuote` against the now-manipulated pool and compute `minPCOut` from it.
4. `CallPRC20DepositAutoSwap`/`depositPRC20WithAutoSwap` executes against the manipulated price, since `minPCOut` was itself derived from that same manipulated price and provides no real protection.
5. Attacker reverses their initial swap in a following transaction in the same block, extracting the price-impact difference from the pool at the protocol's expense.

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-526)
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

```

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
}
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

**File:** e2e-tests/setup.sh (L4214-4222)
```shellscript
  # Surfpool fees are negligible and the SVM broadcaster uses its own compute
  # budget. Keep local Solana gas quotes small so repeated Route 3 tests do not
  # drain the tiny WPC/pSOL AMM pool before the relay observes the event.
  if is_local_testing_env; then
    cast send "$C0" 'updateBaseGasLimitByChain(string,uint256)' \
      "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1" "$LOCAL_SVM_OUTBOUND_BASE_GAS_LIMIT" \
      --rpc-url "$PUSH_RPC_URL" \
      --private-key "$PRIVATE_KEY" >/dev/null || true
  fi
```

**File:** x/uexecutor/keeper/chain_meta.go (L46-61)
```go
// VoteChainMeta processes a universal validator's vote on chain metadata (gas price + chain height).
//
// Rules:
//  1. Each vote is stamped with the current block time (storedAt) when it is recorded
//     and either inserted (new validator) or updated in place (existing validator).
//  2. The oracle is bootstrapped on the first EVM write only after at least
//     chainMetaMinVotesForFirstWrite fresh votes have accumulated. Earlier
//     votes are stored but do not yet drive an on-chain update — this prevents
//     a single validator from defining the oracle's initial values.
//  3. Once bootstrapped (LastAppliedChainHeight > 0), votes whose blockNumber
//     is not strictly greater than entry.LastAppliedChainHeight are rejected —
//     the validator must re-vote with a newer block height.
//  4. When computing medians, only votes whose storedAt is within the last
//     chainMetaVoteStalenessSeconds seconds are considered.
//  5. Price median and chain-height median are computed independently (upper median = len/2).
//  6. After a successful EVM call, LastAppliedChainHeight is updated.
```
