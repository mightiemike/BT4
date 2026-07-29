## Analysis

Mapping the Vader `Synth`-over-mint bug class (minting/settling based on a live pool state read that isn't locked, letting the backing reserve move before/around the mint) onto Push Chain's scope, the closest structural analog is the Uniswap V3 **spot-price swap quote** used to gate the module-driven auto-swap deposits, found in `x/uexecutor/keeper/evm.go` (`GetSwapQuote`, `CallPRC20DepositAutoSwap`) and its callers in `x/uexecutor/keeper/execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `x/uexecutor/keeper/outbound.go` (`applyGasRefund`/`getSwapQuoteForRefund`).

### Title
Spot-price Uniswap V3 quote used to gate module-driven PRC20↔PC auto-swaps enables sandwich/reserve-manipulation extraction - (File: x/uexecutor/keeper/evm.go, execute_inbound_gas.go, outbound.go)

### Summary
`GetSwapQuote` reads `QuoterV2.quoteExactInputSingle` — an instantaneous, single-block AMM price — and the caller applies a fixed 5% slippage buffer (`minPCOut = quote * 95 / 100`) before invoking `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`. Because the quote and the swap execution both happen inside ordinary EVM state that any unprivileged account can move (a normal Uniswap V3 pool with attacker-tradable liquidity), the same "value computed from live/mutable state without locking the underlying reserve" pattern from the Vader `Synth` bug applies: an attacker can move the pool price in the same block immediately before the module's deposit-triggered swap executes, then reverse the trade afterward, extracting value from the protocol-held PRC20/PC liquidity that is supposed to back user deposits and gas refunds. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
For `GAS` and `GAS_AND_PAYLOAD` inbounds, `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` fetch a Uniswap V3 QuoterV2 quote for the exact PRC20 amount, then apply a static 5% slippage tolerance and call `CallPRC20DepositAutoSwap`, which performs the real swap in a separate `DerivedEVMCall`. Likewise, `applyGasRefund` fetches a quote via `getSwapQuoteForRefund` and swaps the excess gas fee back to PC through `CallUniversalCoreRefundUnusedGas`. Both quote and swap execute against the live Uniswap V3 pool reserves in the destination Push Chain EVM state — a pool that ordinary unprivileged users can trade against in the same block. There is no TWAP, no minimum liquidity/depth check, and the 5% band is fixed regardless of pool depth or trade size. This mirrors the Vader flaw: the mint/settlement amount is derived from a pool state that isn't locked or reserved for the operation, so anyone able to move that pool state before the module's swap executes can extract value at the expense of the protocol-held collateral used to back PRC20/PC accounting. [4](#0-3) 

### Impact Explanation
If exploited, an attacker can manipulate the WPC/PRC20 pool price ahead of a validator-triggered `depositPRC20WithAutoSwap` or `refundUnusedGas` call to force the module to accept an unfavorable execution price (still within the 5% band, which can be made arbitrarily lossy on thin pools), extracting protocol-held PC/PRC20 liquidity or reducing what should reach the user's UEA. This corrupts PRC20/native asset accounting and gas-fee/refund accounting invariants that the "Required Impacts" gate calls out explicitly.

### Likelihood Explanation
Exploitability depends entirely on the liquidity depth of the specific Uniswap V3 pool for a given PRC20/WPC pair and on the attacker's ability to place trades in the same block as the module-driven swap (validator-driven execution ordering, not a normal mempool race). On deep, well-arbitraged pools this is low-value; on thin or newly-listed token pools it is directly exploitable by an ordinary EOA with no privileged access, similar to how the original report needed only "nearly 0 liquidity" to be lucrative.

### Recommendation
Use a manipulation-resistant price source (TWAP over multiple blocks, or an external price oracle) to bound `minPCOut`, and/or size the slippage tolerance dynamically based on trade size vs. pool depth rather than a flat 5%, and consider requiring a minimum pool liquidity/TVL threshold before enabling auto-swap for a given PRC20.

### Proof of Concept
1. Registry admin lists a new PRC20/WPC Uniswap V3 pool with shallow liquidity via `x/uregistry`.
2. Attacker observes an inbound GAS/GAS_AND_PAYLOAD transaction about to be finalized (or predicts validator batch timing) and, in the same block, trades against the WPC/PRC20 pool to skew the spot price.
3. `GetSwapQuote` returns a manipulated `amountOut`; `minPCOut = quote * 95/100` is computed from this skewed price and passed to `CallPRC20DepositAutoSwap`.
4. The module's swap executes at the manipulated price (still satisfying the loose 5% band), transferring more PC out of the pool (or delivering fewer PC to the user) than a fair-price execution would.
5. Attacker reverses their initial trade in the same or following block, capturing the price impact as profit funded by protocol-held liquidity. [5](#0-4)

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
```
