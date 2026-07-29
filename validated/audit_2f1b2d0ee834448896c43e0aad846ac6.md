### Title
Uniswap V3 spot-price quote used as swap-execution bound in gas-abstraction and refund auto-swap flows enables price-manipulation extraction of protocol-held funds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report describes `PostAuctionLauncher.finalize()` minting LP tokens against an existing pool without accounting for the pool's current price, letting an attacker who controls the pool price extract most of the deposited tokens. The Push Chain analog is the gas-abstraction auto-swap path: `x/uexecutor` swaps attacker-influenced amounts of a PRC20 gas token for WPC through the chain's own Uniswap V3 pool, using an **instantaneous spot-price quote** (`GetSwapQuote` / `QuoterV2.quoteExactInputSingle`) as the sole basis for the `minPCOut` slippage floor, with no TWAP or manipulation-resistant oracle. The pool is the same protocol-hosted AMM that any unprivileged user can trade against in adjacent blocks.

### Finding Description
`ExecuteInboundGas` (`x/uexecutor/keeper/execute_inbound_gas.go:103-153`) and `gasAndPayloadDepositAutoSwap` (`x/uexecutor/keeper/execute_inbound_gas_and_payload.go:347-379`) both:
1. Call `GetSwapQuote` [1](#0-0)  which reads `QuoterV2.quoteExactInputSingle` via `CallEVM` (a static, same-context spot read) with `SqrtPriceLimitX96: 0`.
2. Derive `minPCOut = quote * 95 / 100` — a fixed 5% slippage band computed directly off that spot quote. [2](#0-1) 
3. Immediately execute the real swap via `CallPRC20DepositAutoSwap` → `depositPRC20WithAutoSwap` on the `UniversalCore`/Uniswap V3 pool. [3](#0-2) 

The same pattern is reused for outbound gas-fee refunds in `getSwapQuoteForRefund` / `applyGasRefund`. [4](#0-3) 

Because the quote and the swap read the same pool state with only a flat 5% tolerance and no TWAP/observation window, an unprivileged attacker who can trade against this protocol-hosted Uniswap V3 pool (the WPC/PRC20 pair used by `UniversalCore`) can push the spot price outside the 5% band before the module's swap executes, then reverse the position afterward — a classic sandwich against an instantaneous-price oracle. Inbound execution runs deterministically in `BeginBlock`/`EndBlock` per `DERIVED_TRANSACTIONS.md`, so the manipulation window is "prior block(s) leading into the block that processes the inbound," which is trivially achievable by anyone submitting ordinary swap transactions against a low-liquidity PRC20/WPC pool (newly listed tokens are especially cheap to move).

### Impact Explanation
The victim of the bad swap is the protocol/user funds being converted: the PRC20 gas-token amount deposited on behalf of a legitimate inbound (or the excess gas being refunded) is swapped into WPC at an attacker-manipulated price, with only 5% floor protection. In a thin pool this materially understates the true value delivered to the UEA recipient (`ExecuteInboundGas`) or the refund recipient (`applyGasRefund`), while the attacker captures the spread by reversing their price-moving trade — a direct value extraction from protocol-controlled/user-owed funds via a pool the module itself trusts as a price source, matching the "corruption of PRC20/native asset accounting, gas fee accounting, refund accounting" allowed-impact category.

### Likelihood Explanation
Any unprivileged actor can create/trade in the relevant Uniswap V3 pool via ordinary EVM transactions on Push Chain — no admin, validator, or TSS privilege is required. Newly onboarded PRC20 tokens (per `uregistry` token configs) are likely to have shallow liquidity, making a >5% price swing cheap. The attack requires no cooperation from validators or Universal Validators, only well-timed ordinary transactions around the block(s) in which gas-abstraction inbounds are processed.

### Recommendation
Replace the instantaneous `quoteExactInputSingle` spot read with a manipulation-resistant price source (e.g., a TWAP over a sufficiently long observation window, or an external chain-meta oracle price cross-checked against the pool quote), and/or widen protection by capping the manipulable slippage tolerance dynamically based on pool depth/liquidity, and consider a `SqrtPriceLimitX96` bound derived from the TWAP rather than 0. Alternatively, disallow auto-swap for tokens/pools below a liquidity threshold and only allow the deposit-without-swap path (`CallPRC20Deposit`) for those.

### Proof of Concept
1. Attacker identifies a PRC20↔WPC Uniswap V3 pool used by `UniversalCore` for a token with shallow liquidity (token freshly whitelisted via `uregistry`).
2. Attacker submits an ordinary swap moving the pool's spot price down for `PRC20→WPC` beyond 5%, in a block prior to the one where a GAS-type inbound will be processed (attacker can trigger their own inbound with `TxType_GAS` to control timing, or simply wait for an unrelated one).
3. When `ExecuteInboundGas` runs in `BeginBlock`, `GetSwapQuote` returns the manipulated (low) quote; `minPCOut` is computed as 95% of that already-depressed value. [5](#0-4) 
4. `CallPRC20DepositAutoSwap` executes the swap at the depressed price, delivering far less WPC to the recipient UEA than the token's fair value. [6](#0-5) 
5. Attacker reverses their initial price-moving trade in the same or a following block, extracting the spread as profit, funded by the value lost from the protocol/gas-abstraction swap.

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

**File:** x/uexecutor/keeper/outbound.go (L259-270)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
}
```
