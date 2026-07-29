### Title
On-chain spot-price AMM quote used as slippage protection lets an attacker sandwich `CallPRC20DepositAutoSwap` / gas refund swaps and drain bridged principal — (`File: x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report (TRST-H-1) shows that letting `receiveAmtMin` be derived from anything the attacker can move (a same-block/same-transaction on-chain AMM state) is not real slippage protection, because the attacker can skew the AMM immediately before the swap and unskew it right after, siphoning the difference. Push Chain's own `uexecutor` module reproduces the identical pattern: it computes the minimum-out for every PRC20→WPC swap by querying the Uniswap V3 `QuoterV2.quoteExactInputSingle` **spot price from the very pool that the actual swap will execute against**, and then simply discounts that quote by a fixed 5% (`minPCOut = quote * 95/100`). There is no external oracle, no TWAP, and no check that the quoted pool is not already manipulated.

### Finding Description
`GetSwapQuote` in [1](#0-0)  performs a live `quoteExactInputSingle` call against the configured Uniswap V3 `QuoterV2`/pool for the `prc20 → wpc` pair, and the result is used, immediately afterward, to compute `minPCOut` at a flat 5% slippage: [2](#0-1) 

The same pattern recurs for the `GAS_AND_PAYLOAD` inbound route: [3](#0-2) 

and for the outbound gas-refund path (`applyGasRefund`), which also fetches a quote and discounts it by 5% before calling `CallUniversalCoreRefundUnusedGas`: [4](#0-3) 

The actual swap is then executed on-chain via `CallPRC20DepositAutoSwap` / `depositPRC20WithAutoSwap`, using the **same pool** the quote was just pulled from: [5](#0-4) 

Because the quote and the swap both read from the same manipulable Uniswap V3 pool state, the "5% slippage" guard only protects against slippage *relative to whatever the pool's current (potentially attacker-skewed) price already is*. It provides no protection against the pool itself being pushed away from fair value beforehand. This is functionally identical to the vault's naive `receiveAmtMin=0`-style trade in TRST-H-1, just with an extra layer of indirection (5% of a manipulable number is still a manipulable number).

An unprivileged attacker (needs no validator, TSS, or admin privilege — only capital and normal EVM transaction access on Push Chain) can:
1. Identify a PRC20↔WPC Uniswap V3 pool with thin liquidity (any freshly whitelisted token, since `uregistry` token onboarding doesn't guarantee deep liquidity).
2. Push the pool price far in one direction with an ordinary swap.
3. Trigger (or wait for) a GAS/GAS_AND_PAYLOAD inbound deposit — their own bridged deposit, or simply time their attack to land while another user's bridged deposit or gas-refund event is finalized by Universal Validators — so that `ExecuteInboundGas` / `ExecuteInboundGasAndPayload` / `applyGasRefund` calls `GetSwapQuote` and `CallPRC20DepositAutoSwap` while the pool is still skewed.
4. Reverse the price skew afterward, capturing the value that would otherwise have been minted as WPC to the victim's UEA (or refunded to the victim), because the deposit/refund got swapped into WPC at the manipulated rate instead of fair value.

Since inbound finalization only requires reaching the 2/3 UV vote threshold (an honest, deterministic process on an observable event), the attacker can predict/observe roughly when the swap will fire and time the pool manipulation accordingly — no single-transaction atomicity or malicious validator is required.

### Impact Explanation
This directly causes unauthorized value extraction (drain) from user/protocol-controlled funds: the PRC20 principal being bridged in, or the excess gas being refunded, is converted to WPC at an attacker-manipulated price instead of a fair one, and the difference is captured by the attacker via pool arbitrage. This falls squarely within the allowed "stealing/draining/permanent loss of user or protocol-controlled funds" impact and is reachable purely by an unprivileged external actor abusing a default transaction path (bridging tokens / triggering gas refunds), with no compromise of validators, TSS, or admin keys required.

### Likelihood Explanation
Likelihood is high for any token pair with modest AMM liquidity relative to attacker capital (which is the common case for freshly onboarded PRC20 tokens or low-volume chains). The attack requires no coordination with validators or insiders and no special timing precision beyond normal blockchain reorg/latency windows.

### Recommendation
Do not derive `minPCOut` (or any slippage bound) purely from a same-pool spot quote taken moments before the swap. Use one or more of:
- A time-weighted average price (TWAP) from the pool with a minimum observation window, rather than `quoteExactInputSingle`'s instantaneous spot price.
- An independent price oracle (e.g., Chainlink, as the referenced report's mitigation used) to bound acceptable output, similar to what was implemented for the vault bug, including staleness and (if relevant) sequencer-uptime checks.
- A protocol-configured maximum absolute slippage/deviation check against a registry-configured reference price (`x/uregistry`), rejecting the swap (falling back to no-swap raw PRC20 deposit) if the live pool price deviates too far from the reference.
- Circuit-breakers / liquidity minimums per pool before allowing auto-swap routes to be used for a given token.

### Proof of Concept
1. Attacker deploys/uses a thin-liquidity PRC20/WPC Uniswap V3 pool already whitelisted in `uregistry`.
2. Attacker swaps a large amount of WPC into the pool, driving the PRC20 price down (or up, depending on desired direction) far below fair value.
3. Attacker (or any user) bridges the target PRC20 token from an external chain (`GAS` or `GAS_AND_PAYLOAD` inbound route). Universal Validators observe and vote honestly; once threshold is reached, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` runs.
4. `GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) returns the skewed quote from the manipulated pool; `minPCOut` is computed as 95% of that skewed number (`execute_inbound_gas.go:142-146`).
5. `CallPRC20DepositAutoSwap` executes the swap against the same skewed pool, converting the bridged PRC20 into an under-valued amount of WPC delivered to the recipient's UEA.
6. Attacker immediately reverses their initial swap, restoring the pool and capturing the arbitrage profit — the difference between fair value and the manipulated conversion rate, extracted from the bridged principal.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-379)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L213-234)
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
```
