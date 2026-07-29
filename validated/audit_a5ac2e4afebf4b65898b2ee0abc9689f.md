## Analysis

The relevant Push Chain analog to the "ineffective slippage protection" bug class lives in the GAS/GAS_AND_PAYLOAD auto-swap path in `x/uexecutor`, not in a UniV3 `sqrtRatioLimit` call directly, but in how the "slippage-protected" `minPCOut` bound is derived.

### Title
Slippage guard for inbound gas auto-swap is derived from the same manipulable spot price it is meant to protect against - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
When a `GAS` or `GAS_AND_PAYLOAD` inbound is finalized, `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` fetch a UniswapV3 QuoterV2 quote for the incoming PRC20→WPC swap and compute `minPCOut = quote * 95 / 100`, then immediately call `CallPRC20DepositAutoSwap` to execute the real swap with that bound [1](#0-0) . The quote and the swap both read/act on the exact same live pool state, in the same DeliverTx, with no independent, user- or protocol-supplied floor value.

### Finding Description
`GetSwapQuote` performs a static `CallEVM` (`commit=false`) against `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (no limit) [2](#0-1) . The returned `amountOut` is then discounted by a flat 5% to produce `minPCOut`, which is passed straight into `CallPRC20DepositAutoSwap` → `depositPRC20WithAutoSwap` on `UNIVERSAL_CORE` [3](#0-2) .

Both calls happen back-to-back inside the same Go function, invoked synchronously from `VoteInbound` → `ExecuteInbound` the moment ballot quorum is reached — i.e., inside the `DeliverTx` of the quorum-finalizing `MsgVoteInbound` [4](#0-3) . There is no block boundary and no external price reference (oracle/TWAP) between the quote and the swap — `minPCOut` is simply a percentage of the pool's current spot price at execution time.

This is the same underlying flaw the H-9 report describes, generalized: the "slippage protection" is not an independent, attacker-resistant bound — it is recomputed from the very state that an attacker can move beforehand. An unprivileged user who can get a transaction ordered before the quorum-finalizing `MsgVoteInbound` in the same block (e.g., via normal gas-price-based mempool prioritization, without needing validator collusion) can swap against the UniswapV3 WPC/PRC20 pool to shift its price. When `ExecuteInboundGas` then runs, `GetSwapQuote` picks up the already-moved price, `minPCOut` is computed as 95% of that same moved price, and the protocol's auto-swap executes against it — satisfying its own "protection" trivially while the depositor's PRC20 is converted to PC at an attacker-favorable rate. The attacker can then reverse their initial trade in a follow-up transaction to realize the extracted value (classic sandwich), and the loss falls on the bridged user's principal converted during onboarding.

### Impact Explanation
This corrupts the amount of native PC credited to the user's UEA during gas-abstraction onboarding (`CallPRC20DepositAutoSwap`'s `to` recipient) and to gas-refund recipients (`applyGasRefund` uses the identical pattern via `getSwapQuoteForRefund` / `CallUniversalCoreRefundUnusedGas`) [5](#0-4) . It is a fund-loss issue reachable purely by an ordinary unprivileged user's own bridging deposit combined with normal same-block trading activity on the AMM — no validator, relayer, or TSS compromise is required.

### Likelihood Explanation
Requires only: (1) liquidity/trading activity on the WPC/PRC20 pool used by `UniversalCore`, and (2) the ability to place an ordinary transaction shortly before the quorum-finalizing vote in the same block, which is a routine MEV/front-running capability on most fee-market mempools and does not require controlling block proposal. Likelihood is moderate, gated by pool liquidity depth and mempool visibility of pending `MsgVoteInbound` transactions.

### Recommendation
Do not derive the slippage floor from a spot quote taken immediately before the swap. Options: use a TWAP-based quote (Uniswap V3 `observe`) with a wider, fixed maximum slippage; allow the acting party (or protocol config) to set an independently bounded `minPCOut` ceiling unrelated to the same-block spot price; or execute the deposit-and-swap step within the same atomic unit as ballot finalization but reference a price checkpoint from a prior block to remove the same-block manipulation window.

### Proof of Concept
1. Attacker monitors the mempool for a `MsgVoteInbound` that will reach quorum for a `GAS`/`GAS_AND_PAYLOAD` inbound.
2. Attacker submits (with sufficient gas price/priority) a large swap on the WPC/PRC20 UniswapV3 pool used by `UniversalCore`, moving the spot price against the pending inbound's swap direction, ordered before the finalizing vote in the same block.
3. `VoteInbound` reaches quorum and calls `ExecuteInboundGas`, which calls `GetSwapQuote` — returning the now-manipulated price — and computes `minPCOut = quote*95/100` from it [6](#0-5) .
4. `CallPRC20DepositAutoSwap` executes against the same manipulated pool state, satisfying `minPCOut` while the depositor receives less PC than fair value.
5. Attacker submits a follow-up transaction reversing their initial trade, capturing the value extracted from the depositor's swap.

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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L148-155)
```go
	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
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
