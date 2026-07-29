## Analysis

The reported bug class — allocating/deallocating liquidity at a manipulable spot price with only an implicit (not attacker-verifiable) slippage tolerance — has a direct analog in Push Chain's `x/uexecutor` gas-abstraction swap logic.

### Where it lives

`ExecuteInboundGas` computes a swap quote from the live Uniswap V3 quoter and immediately executes the swap with a fixed 5% slippage band derived from that same live quote: [1](#0-0) 

The quote itself comes from a spot-price view call into the pool's `QuoterV2`: [2](#0-1) 

The same pattern (fetch quote → apply fixed 95% floor → swap) repeats in the gas+payload inbound flow: [3](#0-2) 

and in the outbound unused-gas refund path, which swaps the leftover gas token back to PC for the recipient: [4](#0-3) 

### Why this mirrors the reported bug class

In the external report, `allocate`/`deallocate` derive token deltas from the *current* pool reserves at execution time with no attacker/caller-supplied bound, so an MEV actor can skew reserves right before the LP action executes and profit at the LP's expense. Here, `minPCOut` is derived the same way — from a quote taken from the *current* Uniswap V3 pool state at the moment the inbound/outbound is executed — rather than from a caller/protocol-supplied bound reflecting fair value (e.g., an oracle price, a TWAP, or a bound set when the inbound was originally observed/voted). The fixed 5% tolerance only bounds slippage *around whatever the pool price happens to be at that instant*; it does nothing to prevent the instant itself from being manipulated.

An unprivileged actor holding tokens in the same PRC20/WPC Uniswap V3 pool can:
1. Swap heavily against the pool right before the module's `GetSwapQuote` → `CallPRC20DepositAutoSwap` (or `CallUniversalCoreRefundUnusedGas`) sequence executes, depressing the WPC-out price for prc20.
2. Let the module's deposit-with-autoswap or gas-refund-with-swap execute at the depressed price (since `minPCOut` is only 95% of that already-depressed quote, it does not block execution).
3. Reverse the swap immediately after to restore the pool, pocketing the difference.

This lets an unprivileged, honest-validator-only attack path corrupt the amount of PC credited to a bridging user's gas top-up or to a refund recipient — i.e., under-crediting native gas value relative to fair value, which falls under "corruption of ... gas fee accounting, refund accounting ... token mapping" in the allowed-impact scope. No malicious validator, relayer, or TSS participant is required — only an ordinary trader interacting with the public AMM pool timed around observable ballot finalization.

### Caveat

Whether this is *practically* exploitable at the same severity as the RMM-01 report depends on details not resolvable purely from static index review of this repository slice:
- The actual block-ordering/mempool visibility of the transaction that triggers ballot finalization (this executes as part of `MsgVoteInbound`/`MsgVoteOutbound` processing, not a directly user-submitted swap-adjacent tx), which determines whether true atomic sandwiching (front-run in same block, back-run in same block) is achievable versus only cross-block manipulation with added risk.
- The liquidity depth of the deployed PRC20/WPC Uniswap V3 pools, which bounds how cheaply the price can be moved.
- Whether `UniversalCore.sol`'s `depositPRC20WithAutoSwap`/`refundUnusedGas` (Solidity side, not present in this Go-only repo slice) impose any additional protections (e.g., deadline enforcement is passed as `0` meaning "use default" — worth checking what that default resolves to).

Because the Solidity contract source (`UniversalCore.sol`, the Handler/Router contracts) is not present in this indexed repository (only its Go ABI bindings are), I cannot fully confirm the on-chain deadline/slippage enforcement semantics from this side alone. [5](#0-4) 

If you want a complete confirmation (including whether the Solidity contracts add any additional TWAP/oracle-based protection), a Devin session with full repo/contract access (including the core-contracts repo, if separate) would be needed — this index only contains the Go node code and ABI strings, not the underlying Solidity implementation.

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

**File:** x/uexecutor/keeper/outbound.go (L213-235)
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
```

**File:** x/uexecutor/types/abi.go (L255-268)
```go
    {
      "type": "function",
      "name": "depositPRC20WithAutoSwap",
      "inputs": [
        { "name": "prc20", "type": "address", "internalType": "address" },
        { "name": "amount", "type": "uint256", "internalType": "uint256" },
        { "name": "target", "type": "address", "internalType": "address" },
        { "name": "fee", "type": "uint24", "internalType": "uint24" },
        { "name": "minPCOut", "type": "uint256", "internalType": "uint256" },
        { "name": "deadline", "type": "uint256", "internalType": "uint256" }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
    },
```
