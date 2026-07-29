No TWAP or configurable max-slippage protections exist anywhere in the codebase—the only price-impact guard is the hardcoded 95/100 (5%) multiplier repeated in three call sites. This confirms the finding.

### Title
Fixed 5% slippage tolerance on spot-price Uniswap V3 quotes lets an attacker sandwich protocol-executed gas-token auto-swaps, draining value from user deposits and gas refunds - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
`x/uexecutor` automatically swaps a user's deposited/refunded gas-token PRC20 into PC through an on-chain Uniswap V3 pool whenever it processes a `GAS`/`GAS_AND_PAYLOAD` inbound or refunds unused outbound gas. The minimum acceptable output (`minPCOut`) is derived from a single spot-price quote (`GetSwapQuote` → `QuoterV2.quoteExactInputSingle`) taken immediately before the swap, with a blanket 5% slippage allowance hardcoded in every call site. There is no TWAP, no oracle cross-check, and no cap on the swap size relative to pool depth, so an attacker can manipulate the pool price in a preceding transaction/block, force the protocol's swap to execute near the worst allowed price, and reverse the manipulation afterward — a classic sandwich — extracting value that should have gone to the recipient's PC balance.

### Finding Description
The vulnerable pattern occurs identically in three places: [1](#0-0) [2](#0-1) [3](#0-2) 

In each case:
1. `GetDefaultFeeTierForToken` and `GetSwapQuote` read the *current* Uniswap V3 pool state via `QuoterV2.quoteExactInputSingle` [4](#0-3) .
2. `minPCOut` is computed as `quote * 95 / 100` — a fixed 5% tolerance regardless of trade size, token, or pool liquidity.
3. `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` immediately executes the swap on-chain using that `minPCOut` as the only protection [5](#0-4) .

Nothing links the quote to a manipulation-resistant price source (no TWAP oracle, no external price feed cross-check), and the tolerance is not configurable per token liquidity depth (confirmed — no `TWAP`/`SlippageBps`/`MaxSlippage` fields exist anywhere in the registry types or params). The trigger for this flow (`MsgVoteInbound` reaching quorum, or an outbound's success/failure vote) is driven by unprivileged, publicly observable on-chain events (a user's deposit on an external chain, or an outbound gas refund), so an attacker watching the chain can predict exactly when the swap will fire and manipulate the underlying PC/PRC20 pool immediately before it executes, then unwind the position immediately after, capturing the price impact as arbitrage profit at the expense of the deposited/refunded funds.

### Impact Explanation
This directly corrupts gas fee/refund accounting and mints/credits less PC than the fair market value of the user's deposited or refunded gas token, permanently reducing the funds credited to the recipient's UEA. Because this is systemic (applies to every `GAS`, `GAS_AND_PAYLOAD` inbound, and every outbound gas refund with a swap leg), a repeatedly-triggered sandwich against a shallow-liquidity PRC20/PC pool can drain meaningful value from ordinary users over time — falling squarely within the "unauthorized...permanent loss...of user or protocol-controlled funds" and "corruption of...gas fee accounting, refund accounting" impact categories.

### Likelihood Explanation
Likelihood is moderate-to-high: any unprivileged actor with capital and a bot watching source-chain gateway events / Push Chain mempool can trigger and sandwich this flow without any privileged access, and the 5% band is generous enough to be profitably exploitable on pools without deep liquidity (which is realistic early in a new PRC20/PC pair's life). No consensus assumptions or malicious validators are required — only ordinary MEV capability against a public AMM pool.

### Recommendation
Replace the fixed 95/100 (5%) constant with a slippage/price-impact bound that scales with trade size relative to pool depth, and/or source the reference price from a manipulation-resistant TWAP rather than a single spot quote taken instants before execution. Consider capping single-swap size as a fraction of pool liquidity, and/or falling back to a no-swap PRC20 deposit path (which already exists as the fallback on swap failure) whenever the computed price impact exceeds a safe threshold.

### Proof of Concept
1. Attacker observes (via source-chain event feed) that a user has bridged a `GAS` inbound with token `T` and amount `A`, or observes a pending `OutboundTx` about to be voted `OBSERVED`/failed with a gas refund.
2. Once quorum is close (2 of 3 votes in, watching mempool for the 3rd `MsgVoteInbound`/`MsgVoteOutbound`), attacker submits a large swap on the `T`/PC Uniswap V3 pool to push the price of `T` down just under 5%.
3. The validator's vote lands, `ExecuteInboundGas`/`applyGasRefund` fires `GetSwapQuote` against the now-manipulated pool and computes `minPCOut = quote*95/100`, then executes `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, receiving PC near the worst tolerated price.
4. Attacker immediately reverses their swap, capturing the price-impact difference as profit, funded by the value that should have been credited to the deposit/refund recipient.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L348-378)
```go
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

**File:** x/uexecutor/keeper/evm.go (L540-593)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

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
}
```
