## Analysis

Push Chain's `x/uexecutor` module implements auto-swap functionality that converts bridged PRC20 gas tokens to native WPC using an on-chain Uniswap V3-style pool, triggered from three flows: `ExecuteInboundGas` (GAS route), `ExecuteInboundGasAndPayload`/`gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD route), and `applyGasRefund`/`getSwapQuoteForRefund` (excess gas refund route). All three compute a `minPCOut` slippage bound as `quote * 95 / 100` where `quote` comes from `GetSwapQuote` calling `QuoterV2.quoteExactInputSingle` immediately before the committed swap executes via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. [1](#0-0) [2](#0-1) [3](#0-2) 

This is the exact analog of the SNX→sUSD swap referenced in the external report, but Push Chain already applies a slippage guard, so I traced whether it is *effective*.

### Title
Sandwich-vulnerable AMM auto-swap: slippage bound is derived from the same manipulable spot quote it is meant to protect against - ([File: x/uexecutor/keeper/evm.go](x/uexecutor/keeper/evm.go))

### Summary
`GetSwapQuote` fetches an instantaneous spot quote from the Uniswap V3 `QuoterV2` (`quoteExactInputSingle`) and the module immediately uses `quote * 95 / 100` as `minPCOut` for the following committed swap via `CallPRC20DepositAutoSwap` or `CallUniversalCoreRefundUnusedGas`. Because both calls read/act on the same current pool state within the same message execution, the 5% tolerance only bounds price movement *between* the quote call and the swap call (which is essentially zero, since they execute back-to-back with no other transaction able to interleave). It provides no protection against pool price manipulation that occurred *before* the quote is fetched. [4](#0-3) [5](#0-4) 

### Finding Description
Uniswap V3 `quoteExactInputSingle` returns a price derived purely from the pool's current reserves/tick state — there is no TWAP, no external oracle, and no check comparing the quote against any independent fair-value reference. An unprivileged user can submit ordinary swap transactions against the underlying prc20/WPC pool (the pool used by `UniversalCore`, addressable via `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`) to shift the pool price in the block(s) immediately preceding the block in which a bridged inbound (GAS or GAS_AND_PAYLOAD) finalizes via validator votes, or immediately preceding processing of an outbound gas refund. Because inbound/outbound voting requires only reaching a public quorum of validator `MsgVoteInbound`/observation transactions (a condition visible in the mempool/consensus process to any observer), the attacker can predict the block in which `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, or `applyGasRefund` will run its auto-swap and pre-position a manipulative trade against the pool beforehand, then reverse it afterward (classic sandwich). Since the quote used for `minPCOut` is fetched at execution time — after the manipulation already happened — the "5% slippage protection" only certifies that the manipulated price won't move much further during the same atomic call; it does nothing to detect or bound the manipulation itself. [6](#0-5) [3](#0-2) 

### Impact Explanation
Every PRC20-gas-token → WPC conversion routed through the auto-swap (deposit of bridged gas on GAS/GAS_AND_PAYLOAD inbounds, and excess-gas refund conversions) can be executed against a manipulated price, causing the depositor/recipient UEA or the refund recipient to receive fewer PC/WPC tokens than fair value while the attacker extracts the spread from the pool. This is a direct, unprivileged-attacker-reachable loss of user/protocol-controlled funds through the universal execution path, matching the in-scope impact category of "stealing ... permanent loss ... of user or protocol-controlled funds" via corrupted PRC20/native asset accounting.

### Likelihood Explanation
Likelihood depends on the depth/liquidity of the actual prc20/WPC pool and the size of individual bridged deposits, and on whether the attacker can reliably land a front-run transaction ahead of the finalizing vote transaction in the same or an adjacent block under an honest, non-colluding proposer (ordinary mempool submission, no validator collusion required). Given Push Chain's pools are newly deployed (e.g., via the e2e swap-AMM setup script) they are likely to have thin liquidity, making meaningful price impact from a single well-funded trade plausible, while the timing predictability (quorum-based vote finalization is publicly observable) makes the "when" side of the sandwich straightforward. This keeps likelihood moderate rather than trivial, since it needs pool-specific liquidity conditions and reasonably tight tx-inclusion timing.

### Recommendation
Do not derive `minPCOut` solely from a same-call spot quote (`QuoterV2.quoteExactInputSingle`). Instead:
- Use a manipulation-resistant reference price (e.g., a TWAP over a meaningful window, or a governance/config-set reference exchange rate) to bound the acceptable output, independent of the pool's instantaneous state.
- Alternatively, cap the maximum swappable amount per block relative to pool depth, or route conversions through a protocol-controlled liquidity mechanism that isn't subject to public AMM manipulation.
- Consider widening monitoring/alerting on large realized slippage between expected (config/TWAP) price and executed price for auto-swap transactions so that anomalies can be investigated post-hoc.

### Proof of Concept
1. Attacker identifies the prc20/WPC Uniswap V3 pool used by `UniversalCore` (address resolvable via `GetUniversalCoreWPCAddress`/`defaultFeeTier`).
2. Attacker observes validator `MsgVoteInbound` transactions for a pending GAS or GAS_AND_PAYLOAD inbound reaching quorum (2/3+), predicting the exact block where `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` will call `GetSwapQuote` + `CallPRC20DepositAutoSwap`.
3. In a transaction ordered before the quorum-finalizing vote transaction, attacker swaps a large amount of WPC into the pool for prc20 (or vice versa depending on desired skew), moving the pool's instantaneous price.
4. The module's `GetSwapQuote` call (executed after the manipulation, in the same or next block) returns a quote reflecting the skewed price; `minPCOut = quote * 95 / 100` is computed from this already-bad price and passed to `CallPRC20DepositAutoSwap`, so the check trivially passes.
5. Attacker submits a reversing trade afterward, restoring the pool price and capturing the spread extracted from the module's swap, while the bridged depositor's UEA receives less WPC/PC than fair market value would provide. [4](#0-3)

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

**File:** x/uexecutor/keeper/evm.go (L540-592)
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
```
