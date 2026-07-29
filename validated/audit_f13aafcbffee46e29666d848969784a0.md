## Analysis

The DSC report's core issue: a liquidation function computes an expected output amount but has **no lower-bound check** against the value actually realized at execution time, so intra-block price manipulation (oracle push + MEV sandwich) lets an attacker extract value from the transaction between quote and settlement.

The closest Push Chain analog is in `x/uexecutor` module's **on-chain PRC20→PC auto-swap path**, used both for `GAS`/`GAS_AND_PAYLOAD` inbound processing and for outbound gas-fee refunds. Unlike the DSC bug, this code *does* compute a `minPCOut` floor — but the floor itself is derived from a manipulable spot quote fetched in the very same state-transition, using a fixed 5% slippage tolerance, exposing users to the same class of MEV extraction the report describes. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Fixed 5% slippage tolerance on Uniswap V3 spot-quote auto-swaps allows MEV sandwich extraction from user deposits and gas refunds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
When `x/uexecutor` executes a `GAS` / `GAS_AND_PAYLOAD` inbound, or refunds unused destination-chain gas, it calls `GetSwapQuote` against a Uniswap V3 `QuoterV2` contract to get a spot price, then immediately executes `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` with `minPCOut = quote * 95 / 100`. Because the quote is a live pool spot price (not a TWAP or external oracle) and the tolerance is a flat 5%, an unprivileged attacker can sandwich the underlying Uniswap V3 pool around the block containing the quorum-finalizing `MsgVoteInbound`/`MsgVoteOutbound` transaction, pushing the spot price down before quote-fetch and reversing it afterward, extracting up to ~5% of the swapped value from ordinary users' deposits and gas refunds on every occurrence.

### Finding Description
`GetSwapQuote` calls `quoteExactInputSingle` (commit=false) on the configured QuoterV2 to price a `prc20 → wpc` swap at current pool state [2](#0-1) . The result feeds directly into `minPCOut` with a hardcoded 5% cushion:

```
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
```

This pattern is repeated in three places reachable from ordinary user activity:
1. `ExecuteInboundGas` — GAS inbound top-up [4](#0-3) 
2. `gasAndPayloadDepositAutoSwap` — GAS_AND_PAYLOAD inbound [5](#0-4) 
3. `applyGasRefund` — outbound excess gas-fee refund [3](#0-2) 

Because the quote-then-swap sequence executes as part of processing a single Cosmos SDK transaction (the vote that crosses quorum, triggered inside `MsgVoteInbound`/`MsgVoteOutbound` handling), it is atomic with respect to *other* transactions in the same block only in the sense that no interleaving is possible *between* the quote call and the swap call themselves. However, the underlying Uniswap V3 pool state that the quote reads is itself just another piece of on-chain state that any unprivileged actor can move with an ordinary EVM transaction placed immediately before the quorum-triggering transaction in the same block (front-run), and reversed immediately after (back-run) — a classic sandwich. The 5% band is a fixed, protocol-chosen tolerance, not a user-supplied minimum reflecting the price the depositor actually expected; it does not defend against — and in fact defines the maximum size of — value extractable by exactly this sandwich pattern.

This mirrors the report's root cause precisely: a settlement path derives its "safety" bound from a spot price observed immediately before execution rather than from a value the affected party actually committed to, so an attacker who can manipulate that spot price in the same block profits at the affected party's expense.

### Impact Explanation
Every GAS/GAS_AND_PAYLOAD inbound and every outbound gas-fee refund that routes through the auto-swap path is exposed to up to a 5% value loss extracted by an unprivileged MEV actor sandwiching the underlying Uniswap V3 pool. This is a direct, repeatable value transfer from ordinary users (and, for the refund path, the protocol's `UniversalCore` refund accounting) to an attacker, on paths reachable by default user deposit/withdrawal flows with no privileged actor required — falling squarely within "corruption of PRC20 or native asset accounting... refund accounting" and "unauthorized... state transitions in universal execution flows."

### Likelihood Explanation
Likelihood is high for any pool with realistic liquidity depth relative to trade size, since sandwiching a spot-price quoter is a well-understood, low-cost MEV technique requiring only two ordinary EVM transactions bracketing the block containing the target vote transaction. No validator, relayer, or admin collusion is needed — a plain external actor with capital and mempool visibility suffices.

### Recommendation
Do not derive `minPCOut` solely from a same-block spot quote with a flat percentage cushion. Options:
- Use a manipulation-resistant price source (e.g., a TWAP over multiple blocks, or the ChainMeta/gas-price oracle already used elsewhere in this module) to bound acceptable slippage instead of an instantaneous `QuoterV2` read.
- Let the *original* inbound/outbound event (set on the source/destination chain by the user, at the time of intent) carry an explicit user-committed minimum output, analogous to the report's recommended `minimumOutputTokens` parameter, so the check reflects what the user actually agreed to rather than a value computed adversarially close to execution.
- Consider tightening or dynamically sizing the tolerance based on observed pool depth, and/or splitting large swaps to reduce sandwich profitability.

### Proof of Concept
1. Attacker monitors the Push Chain mempool/consensus for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will cross quorum and trigger `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` for a sizeable `amount`.
2. Attacker submits a large swap against the same Uniswap V3 pool (`prc20 → wpc`) in an earlier position of the same block, depressing the spot price the pool would report.
3. The quorum-crossing transaction executes: `GetSwapQuote` returns the manipulated (lower) price, `minPCOut = quote * 95/100` is computed from that already-manipulated value [6](#0-5) , and `CallPRC20DepositAutoSwap` executes the deposit/swap at the depressed rate — the recipient UEA receives materially less PC than fair value, while still satisfying the (already-manipulated) `minPCOut` check.
4. Attacker submits a reverse trade immediately after in the same block to restore the pool price and realize the captured spread as profit.
5. Repeat across any GAS/GAS_AND_PAYLOAD inbound or gas-fee refund event of sufficient size.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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
