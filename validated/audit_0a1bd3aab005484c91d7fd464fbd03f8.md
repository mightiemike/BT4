### Title
Self-referential Uniswap V3 quote used as its own slippage bound in `x/uexecutor` auto-swap paths allows price-manipulation drain of protocol-owned liquidity - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The Ajna bug used the volatile, manipulable "auction price" instead of an anchored reference ("bucket price") to compute a protective factor (BPF), letting a caller extract more value than intended. Push Chain's `x/uexecutor` module has the same class of defect in its PRC20→WPC auto-swap paths: it derives `minPCOut` (the slippage floor) from the very same instantaneous `QuoterV2.quoteExactInputSingle` call it is supposed to protect against, rather than from an independent/anchored price. An unprivileged attacker who moves the pool price with an ordinary swap transaction can make the "protection" self-fulfilling and drain value from the protocol-owned WPC/PRC20 pool during gas-deposit auto-swaps and gas-refund auto-swaps.

### Finding Description
Three call sites compute `minPCOut` the same way: [1](#0-0) [2](#0-1) [3](#0-2) 

In each case:
1. `GetSwapQuote` performs a static `CallEVM` to `QuoterV2.quoteExactInputSingle`, reading the *current* on-chain AMM reserves/price for the PRC20/WPC pool: [4](#0-3) 
2. `minPCOut` is computed as `quote * 95 / 100` — i.e., 95% of that same just-read spot price.
3. `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` then perform the real swap on the Uniswap V3 pool, bounded only by `minPCOut`: [5](#0-4) 

Because the "reference price" and the "executed price" are read from the identical pool state (no TWAP, no independently registered/oracle price, no chain-config anchor), this is functionally equivalent to no slippage protection at all against price manipulation that occurs before the module reads the quote. This exactly mirrors the Ajna root cause: a factor meant to protect the recipient is derived from a price that itself can be pushed adverse-to-fair-value, instead of from a stable reference. `x/uregistry`/`UniversalCore` do maintain chain-level gas price oracles (`GetGasPriceByChain`, `ChainMeta`) for cross-chain gas pricing — analogous "anchored" data exists in the system, but it is not used here as a sanity check on the swap quote.

Reachability: an unprivileged external attacker can submit an ordinary EVM transaction against the WPC/PRC20 pool (a large one-sided swap) to push the pool price in the direction that benefits them, then have their own (or observe someone else's) `GAS`/`GAS_AND_PAYLOAD` inbound, or a pending outbound gas-refund, get processed while the pool is still in the manipulated state. Both `GetSwapQuote` and the real swap read the manipulated state atomically within the same keeper call, so the 5% band never protects against anything but rounding — it does not protect against a moved market.

### Impact Explanation
A successful manipulation lets an attacker extract PC (or the reverse, PRC20) from the protocol-owned Uniswap V3 pool used by `UniversalCore.depositPRC20WithAutoSwap` / `refundUnusedGas`, at the expense of the pool's liquidity (protocol-controlled funds) or of other users whose deposits/refunds are swapped at an unfairly poor rate during the manipulated window. This falls under "draining ... of protocol-controlled funds" and "corruption of ... gas fee accounting / refund accounting" in the allowed impact set, since the computed `minPCOut` no longer reflects fair value once the pool is manipulated.

### Likelihood Explanation
Medium. It requires the attacker to have enough capital or a thin-enough pool to move the price meaningfully within the ~5% band, and to time their attack around an inbound/outbound processing event (inbound gas deposits and gas refunds happen routinely as part of normal user activity, so the attack surface is continuously available, not one-off). No validator or admin collusion is required — only ordinary EVM transactions from an unprivileged account.

### Recommendation
Do not derive the slippage bound from the same live spot quote that will be used to execute the swap. Options:
- Use a TWAP-based quote (Uniswap V3 pools support time-weighted observations) instead of `quoteExactInputSingle`'s instantaneous quote.
- Cross-check the AMM quote against the chain-registered oracle gas price / an admin-configured max-deviation band before accepting `minPCOut`.
- Widen and harden the slippage check by comparing the executed price to a stored reference price (e.g., last N blocks' average) and reverting/falling back to the no-swap path if the deviation exceeds a safe threshold.

### Proof of Concept
1. Attacker identifies the WPC/PRC20 pool address for a token used by frequent gas-only inbound deposits (`GAS` / `GAS_AND_PAYLOAD` TxType).
2. Attacker submits a large one-sided swap into the pool to depress/inflate the pool price beyond normal market rate.
3. A normal user's inbound (or the attacker's own) reaches `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`; `GetSwapQuote` reads the now-manipulated reserves and returns a skewed quote; `minPCOut = quote*95/100` is computed from that same skewed number, so it "passes" trivially.
4. `CallPRC20DepositAutoSwap` executes at the manipulated price, converting the deposit's PRC20 into an incorrect amount of PC drawn from the protocol pool.
5. Attacker reverses their initial swap (or lets arbitrageurs do so), realizing a profit extracted from the pool/protocol at the expense of the swap executed in step 4.

Note: I was not able to fully trace whether `ExecuteInboundGas` fires synchronously inside the finalizing `MsgVoteInbound` transaction or from a `BeginBlock`/`EndBlock` hook (index truncated before I could confirm via `x/uexecutor/keeper/execute_inbound.go`); this affects exactly how tightly an attacker can time the manipulation relative to block boundaries, but does not change the core finding that the slippage bound is self-referential and therefore not a real protection against price manipulation. A full Devin session with repository access is recommended to confirm the exact trigger point and to size real-world pool depth/attack cost before treating this as production-critical.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-146)
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

**File:** x/uexecutor/keeper/outbound.go (L213-223)
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
