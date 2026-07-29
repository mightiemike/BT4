### Title
Gas-abstraction auto-swap uses manipulable spot AMM price as both quote and slippage bound, enabling MEV extraction from user deposits - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The reported bug class is "spot-price oracle vulnerable to single-block manipulation, then reused to compute an economic guardrail (slippage/borrow power) that is trusted as if it were a manipulation-resistant TWAP." Push Chain's own gas-abstraction path (`ExecuteInboundGas`) reproduces this exact pattern: it fetches a Uniswap V3 QuoterV2 spot quote and uses it, with only a static 5% haircut, as the minimum-output slippage bound for a real swap executed against the very same pool.

### Finding Description
When a `GAS` (or `GAS_AND_PAYLOAD`) inbound is finalized by honest UV votes, `ExecuteInboundGas` deposits the user's bridged PRC20 and auto-swaps it into WPC via the `UniversalCore`/Handler contract: [1](#0-0) 

The reference price comes from `GetSwapQuote`, which calls Uniswap V3 `QuoterV2.quoteExactInputSingle` — a spot simulation against the pool's *current* reserves/liquidity, with no time-weighting: [2](#0-1) 

That spot quote is then discounted by a fixed 5% and passed straight into the real swap executed on the same pool via `depositPRC20WithAutoSwap`: [3](#0-2) [4](#0-3) 

This mirrors the `ERC4626Oracle` flaw precisely: `previewRedeem`/`convertToAssets` there and `quoteExactInputSingle` here are both derived from spot pool state that can be pushed away from the true market price within the timeframe an attacker controls (a large swap immediately preceding the block/transaction in which the quote+swap pair executes), and the "protection" value (LP price / `minPCOut`) is computed from that same manipulated state, so it protects nothing — it only bounds drift *between* the quote call and the swap call, both of which read the identical, already-manipulated pool.

Because `quoterAddr`/`wpcAddr`/`fee` are read from mutable `UniversalCore` config each call and no staleness/TWAP check exists anywhere in this path, an unprivileged attacker who can influence transaction ordering (e.g., submit their own large swap against the WPC/PRC20 pool immediately ahead of the block containing the vote-finalization tx that triggers `ExecuteInboundGas` for their own or another user's inbound) can push the pool price down, causing the honest user's deposited PRC20 to be swapped for far less WPC than fair value, while the attacker's own reversing trade recaptures the difference as profit — draining value from the depositor and/or the protocol-owned liquidity used to service every gas-abstraction deposit.

### Impact Explanation
This falls under "unauthorized release ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting ... token mapping" in the allowed-impact gate: every `GAS`/`GAS_AND_PAYLOAD` inbound routes user funds through this unprotected spot-price swap, so an unprivileged attacker can systematically extract value from ordinary users' gas-abstraction deposits without compromising any validator, relayer, or admin key.

### Likelihood Explanation
Medium. It requires the attacker to hold/borrow enough capital to move the specific WPC/PRC20 pool and to win transaction ordering against the finalizing vote tx within the same or an adjacent block — feasible via ordinary mempool front-running/back-running, not privileged access, and repeatable against every gas-abstraction inbound that uses the same pool.

### Recommendation
Do not use the raw QuoterV2 spot quote as the sole reference price for slippage protection. Cross-check it against a manipulation-resistant reference (e.g., Uniswap V3 TWAP observation over a meaningful window, or the existing `ChainMeta`/gas-price oracle where applicable) and reject/revert the swap if the spot quote deviates beyond a bounded tolerance from that reference, rather than deriving `minPCOut` purely from the same spot value being swapped against.

### Proof of Concept
1. Attacker identifies the WPC/PRC20(gas-token) Uniswap V3 pool used by `UniversalCore` for a given `defaultFeeTier`.
2. Attacker (or a colluding relayer, unprivileged w.r.t. consensus) submits a large one-sided swap against that pool to depress the PRC20→WPC price, timed to land in the block immediately preceding — or same block, ahead of — the tx that finalizes an honest user's `GAS`/`GAS_AND_PAYLOAD` inbound.
3. `ExecuteInboundGas` calls `GetSwapQuote` (`x/uexecutor/keeper/evm.go:502`), which reads the now-manipulated spot price and returns a depressed quote.
4. `minPCOut` is computed as 95% of this depressed quote (`x/uexecutor/keeper/execute_inbound_gas.go:143-145`) and `CallPRC20DepositAutoSwap` executes the real swap against the still-manipulated pool, so the swap succeeds but yields far less WPC to the user's UEA than fair value.
5. Attacker reverses their initial trade, capturing the price difference as profit extracted from the value that should have gone to the depositing user / protocol liquidity.

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
