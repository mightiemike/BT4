Confirmed: fee tier is protocol-configured (not attacker-controlled), and the quote/slippage bound both derive purely from the same-block Uniswap V3 `QuoterV2.quoteExactInputSingle` spot price with no TWAP/oracle cross-check.

### Title
Slippage protection anchored to manipulable same-block spot price allows sandwich extraction on inbound GAS/FUNDS_AND_PAYLOAD auto-swaps - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/evm.go)

### Summary
Push Chain's fix for the TRST-M-11 class of bug (no slippage protection on cross-chain swaps) computes a `minPCOut` bound before every PRC20→WPC auto-swap: `ExecuteInboundGas` [1](#0-0) , `gasAndPayloadDepositAutoSwap` [2](#0-1) , and the gas-refund swap-back leg in `applyGasRefund` [3](#0-2)  all call `GetSwapQuote` and then derive `minPCOut = quote * 95 / 100`. However `GetSwapQuote` reads the **instantaneous spot price** from Uniswap V3 `QuoterV2.quoteExactInputSingle` in the very same call chain, immediately before the real swap executes: [4](#0-3) . There is no TWAP, no external oracle, and no bound tied to an independent reference price — the "protection" is only a fixed percentage off of whatever price the pool happens to show at that instant.

### Finding Description
Because the quote and the swap execution both read the pool's current spot price in the same transaction, the 5% band only protects against price drift *between* the quote call and the swap call within that same atomic execution (which is effectively zero, since they're sequential calls in one Go function with no external interleaving). It does **not** protect against an attacker who manipulates the pool's spot price *before* the quorum-triggering vote transaction (`MsgVoteInbound`/`MsgVoteOutbound`) lands.

An unprivileged actor watching the mempool for the third (quorum-completing) validator vote can front-run it with a large swap against the same PRC20/WPC Uniswap V3 pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, pushing the spot price down. When the vote transaction executes immediately afterward, `GetSwapQuote` returns a quote that already reflects the attacker's manipulated price, so `minPCOut` (95% of an already-degraded number) is trivially satisfied by `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`. The user's PRC20 deposit is then swapped into PC at the manipulated rate, and the attacker back-runs with an opposite trade to restore the price and capture the price-impact profit that should have gone to the user.

This affects three flows that carry real user or protocol-controlled value:
- Gas-abstraction inbound auto-swap (user's bridged token → PC gas top-up) [5](#0-4) 
- `GAS_AND_PAYLOAD` inbound auto-swap [6](#0-5) 
- Outbound gas-refund swap-back (excess gas fee → PC) [7](#0-6) 

### Impact Explanation
Users' bridged funds are converted at an attacker-manipulated exchange rate rather than a fair market rate, resulting in a partial loss of value on every gas-abstraction and gas-refund auto-swap that goes through the on-chain AMM. Because these swaps are module-originated (`isModuleSender=true`) and execute automatically once validator quorum is reached, the user has no way to set their own slippage tolerance or cancel the trade — the only guard is the protocol-computed 5%, which is exactly the amount an attacker can safely extract each time via sandwiching against pool depth deep enough to move price ~5% and back.

### Likelihood Explanation
Exploitability depends on the depth/liquidity of the PRC20/WPC pool relative to the swap size and on the attacker's ability to front-run the quorum-completing vote transaction (an ordinary, unprivileged, publicly visible transaction). This is standard MEV/sandwich behavior against on-chain AMMs and requires no privileged role, no validator collusion, and no compromise of TSS/UV honesty — only capital and mempool visibility, which is within the "unprivileged external attacker" threat model.

### Recommendation
Do not derive `minPCOut` from a spot-price quote taken in the same execution as the swap. Use a manipulation-resistant reference price (e.g., a TWAP over multiple blocks, or an off-chain-attested price bound submitted and validated similarly to other UV-attested data) so that the slippage bound reflects a price an attacker cannot move within a single block/transaction. Alternatively, cap the maximum single-swap size relative to pool liquidity, or introduce a circuit breaker comparing the spot quote against a longer-window average before allowing the auto-swap to proceed.

### Proof of Concept
1. Attacker identifies a pending `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will push a `TxType_GAS` or `TxType_GAS_AND_PAYLOAD` inbound (or a gas refund) over quorum, triggering `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund`.
2. Attacker submits a transaction with higher gas/priority that swaps a large amount of PRC20 into WPC (or vice versa) on the same Uniswap V3 pool referenced by `GetUniversalCoreQuoterAddress`, moving the spot price against the pending deposit-swap direction.
3. The quorum-completing vote transaction executes; `GetSwapQuote` (`x/uexecutor/keeper/evm.go:500-537`) returns a quote computed on the now-skewed pool state; `minPCOut = quote * 95/100` is computed from that skewed value and easily satisfied by `CallPRC20DepositAutoSwap`.
4. The user's PRC20 is converted to PC at the manipulated rate.
5. Attacker submits a follow-up transaction reversing the initial swap, restoring the pool price and pocketing the price-impact difference extracted from the user's converted funds.

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

**File:** x/uexecutor/keeper/evm.go (L500-537)
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
```
