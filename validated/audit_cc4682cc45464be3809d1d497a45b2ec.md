## Analysis

The Basin bug is fundamentally: **a price-dependent action reads a manipulable spot value that the attacker can move to their advantage within the same atomic execution window, using a slippage/quote bound that is itself derived from that same manipulated value, then reverses the manipulation to extract value.**

The matching pattern in Push Chain is the `GAS` / `GAS_AND_PAYLOAD` inbound execution path, which fetches a live Uniswap V3 spot quote and immediately consumes it as the swap's own slippage bound, all triggered deterministically and synchronously by the exact transaction that finalizes the inbound ballot — giving an attacker perfect knowledge of when the swap will execute and full ability to sandwich it in the same block.

### Title
Sandwichable spot-price swap quote used as its own slippage bound in `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`ExecuteInboundGas` and `ExecuteInboundGasAndPayload` compute `minPCOut` slippage protection for the module's own PRC20→PC auto-swap by calling `GetSwapQuote`, which reads the Uniswap V3 `QuoterV2.quoteExactInputSingle` spot price at execution time [1](#0-0) , then immediately uses `quote * 95 / 100` as the minimum acceptable output for `CallPRC20DepositAutoSwap` [2](#0-1) . Because the quote and the swap execute back-to-back off the *same* manipulable instantaneous pool state, the slippage bound offers no real protection against price manipulation — an attacker can move the pool price before this sequence runs and reverse it after, capturing the difference.

### Finding Description
`ExecuteInbound` is invoked synchronously inside `VoteInbound`, the exact `MsgVoteInbound` message handler whose vote pushes the ballot to quorum [3](#0-2) . This makes the block in which the deposit-and-autoswap executes fully predictable to anyone watching the mempool/ballot state: once `votesNeeded` is one vote away, the next `MsgVoteInbound` deterministically triggers `GetSwapQuote` + `CallPRC20DepositAutoSwap` in that same block [4](#0-3) .

Within that flow:
1. `GetSwapQuote` performs a `quoteExactInputSingle` call against the live pool reserves at the current block state — a spot price, not a TWAP [5](#0-4) .
2. The result is used directly to compute `minPCOut = quote * 95 / 100` [6](#0-5) , i.e., the slippage floor is derived from the very value being manipulated.
3. `CallPRC20DepositAutoSwap` then executes the real swap using that bound [7](#0-6) .

An unprivileged attacker who can trade against the same Uniswap V3 pool (PRC20/WPC) can:
- Submit a large swap transaction ordered immediately before the quorum-triggering `MsgVoteInbound` in the same block, depressing the PRC20→WPC price.
- Let the module's quote-then-swap sequence execute against this depressed price (still within the 5% slippage window it just computed from the manipulated price, so it never reverts).
- Submit a reverse swap immediately after (same or next block) to restore the price, netting the value that would otherwise have gone to the deposited/converted PC output (used to fund the user's UEA gas or gas+payload flow).

This is the same root-cause class as the Basin report: a price/reserve-dependent value is manipulated within the same execution window as the dependent action, and the slippage/oracle guard is computed from the manipulated value itself rather than a manipulation-resistant reference (TWAP), enabling manipulate → extract → revert.

The same pattern also affects the gas-refund swap path, `applyGasRefund`/`getSwapQuoteForRefund`, which follows an identical quote→minPCOut→swap sequence for excess gas refunds [8](#0-7) , though this path is triggered on outbound vote finalization rather than inbound.

### Impact Explanation
Every `GAS` and `GAS_AND_PAYLOAD` inbound (and every successful-outbound gas refund with excess) routes user or protocol PRC20 funds through this sandwichable auto-swap. An attacker able to move the underlying Uniswap V3 pool price can systematically extract value from users' deposited gas funds during the deposit-and-swap step, since the "slippage protection" tracks the manipulated price rather than a fair/TWAP reference. This constitutes a fund-drain vector against user-controlled deposits reachable through the ordinary inbound-voting/execution path, matching the in-scope impact of "draining ... user or protocol-controlled funds" via "corruption of ... PRC20 or native asset accounting" and "gas fee accounting."

### Likelihood Explanation
The trigger requires only: (a) the ability to submit ordinary swap transactions against the PRC20/WPC Uniswap V3 pool (any unprivileged EVM user), and (b) predictable knowledge of when the quorum-finalizing `MsgVoteInbound` lands, which is directly observable from `PendingInbounds`/ballot vote counts on-chain — no privileged access, node compromise, or validator collusion required. The likelihood is bounded mainly by the depth/liquidity of the specific PRC20/WPC pool and the 5% band, but for any newly-listed or thin-liquidity PRC20 pool this is straightforwardly exploitable with a single-block sandwich.

### Recommendation
Do not derive the slippage bound (`minPCOut`) from the same instantaneous quote that the swap itself will execute against. Instead:
- Use a manipulation-resistant reference price (e.g., a TWAP over N blocks/observations, or an externally configured/registry-anchored reference price) to bound acceptable output, independent of the spot state at execution time.
- Alternatively, cap the maximum single-block price impact allowed for this module-driven swap, and/or route these deposits through a path with circuit breakers tied to observed pool depth.
- Consider decoupling the trigger block from the vote-finalizing transaction (e.g., process inbound execution in `EndBlocker` after a fixed delay) so the execution block is not perfectly predictable to a front-runner, though this alone does not fix the core spot-price-as-its-own-bound issue.

### Proof of Concept
1. Attacker monitors `PendingInbounds`/ballot state for a `GAS` or `GAS_AND_PAYLOAD` inbound one vote away from quorum (`votesNeeded` in `VoteOnInboundBallot`).
2. Attacker submits Tx A: a large `swapExactTokensForTokens`-equivalent trade on the PRC20/WPC Uniswap V3 pool that depresses the PRC20 price, ordered immediately before the quorum-finalizing `MsgVoteInbound`.
3. The quorum vote lands; `VoteInbound` → `ExecuteInbound` → `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` runs `GetSwapQuote` against the now-depressed pool, computes `minPCOut = quote*95/100`, and calls `CallPRC20DepositAutoSwap`, executing the deposit swap at the manipulated price (still passes its own just-computed 5% bound).
4. Attacker submits Tx B: a reverse trade restoring the pool price, capturing the value difference extracted from the module's swap.
5. Net effect: the user's gas/payload deposit is converted to PC at a manipulated unfavorable rate; the attacker profits the difference — repeatable per targeted inbound.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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

**File:** x/uexecutor/keeper/voting.go (L48-58)
```go
	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
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
