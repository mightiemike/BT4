### Title
Sandwichable spot-price auto-swap in GAS/GAS_AND_PAYLOAD inbound execution forces users into unfavorable PRC20→WPC conversions - (File: `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
Inbound deposits of type `GAS` and `GAS_AND_PAYLOAD` are automatically swapped from the deposited PRC20 asset into the native gas token (WPC) via a Uniswap V3 pool, with no user-set slippage parameter. The minimum output is derived from a single spot quote (`GetSwapQuote`) with a fixed, generous 5% tolerance, allowing an unprivileged attacker to manipulate the pool's spot price around the block in which the module-driven swap executes and extract value from the user's forced conversion — a sandwich attack directly analogous to the Beanstalk deltaB manipulation, which also relied on a spot-price signal being manipulable around the victim's forced action.

### Finding Description
When an inbound of `TxType_GAS` or `GAS_AND_PAYLOAD` is finalized, `ExecuteInboundGasAndPayload` calls `gasAndPayloadDepositAutoSwap` to convert the user's deposited PRC20 into the chain's native gas token so the user's UEA can pay gas: [1](#0-0) 

Inside this function:
1. `GetSwapQuote` performs a `CallEVM` (uncommitted/static) call to `QuoterV2.quoteExactInputSingle`, reading the *current spot price* of the PRC20/WPC Uniswap V3 pool [2](#0-1) .
2. `minPCOut` is computed as `quote * 95 / 100` — a flat 5% slippage tolerance, with no per-user configuration and no TWAP or manipulation-resistant price source [3](#0-2) .
3. `CallPRC20DepositAutoSwap` then executes the swap on-chain with this `minPCOut` as the only protection [4](#0-3) .

This flow is *not* user-initiated — it is a module-originated `DerivedEVMCall` triggered automatically the moment the corresponding inbound ballot is finalized. The user (victim) has no control over the timing of the swap or its slippage tolerance. Because the quoted price is a spot price of an on-chain AMM pool, an unprivileged attacker can:
1. Submit a normal EVM transaction to swap heavily in the PRC20/WPC pool, moving the spot price against the pending inbound's expected direction, immediately before the inbound-finalizing transaction (`MsgVoteInbound` quorum-reaching vote, or the following `EndBlock`/execution) is included.
2. When `ExecuteInboundGasAndPayload` runs, `GetSwapQuote` reads the manipulated price, and the swap still clears the 5% `minPCOut` band (since the band is derived from the same manipulated quote), causing the victim's deposited PRC20 to be converted at a worse-than-fair rate.
3. The attacker reverses their swap afterward, realizing the difference as profit extracted from the victim's auto-swap output.

This mirrors the reported Beanstalk pattern precisely: a value used to gate/compute an outcome for another user's unavoidable action (deltaB → penalty decision, here spot price → minPCOut) is manipulated immediately around that action by an attacker who profits from the round-trip, and the victim cannot opt out or set their own tolerance.

### Impact Explanation
Users who deposit assets cross-chain with `GAS`/`GAS_AND_PAYLOAD` type inbounds have no choice but to go through this auto-swap; a sandwiching attacker can force them to receive up to 5% less native gas token than fair market value on every such deposit, which is a direct, repeatable value extraction from user funds via the module's own execution path. Given this swap happens for essentially every gas-funding deposit, this is a systemic drain vector on protocol/user-controlled value routed through `x/uexecutor`.

### Likelihood Explanation
This requires no privileged access — any address able to submit ordinary EVM swap transactions against the PRC20/WPC pool can execute the attack, and inbound finalization timing (quorum reached in a specific block) is observable from the public mempool/chain state, making the attack practically executable by any unprivileged actor for pools with modest liquidity.

### Recommendation
Do not rely solely on a same-block spot quote with a fixed 5% band for a module-forced swap. Consider: computing `minPCOut` from a manipulation-resistant reference (e.g., TWAP oracle or `uregistry`-configured expected price) rather than the raw `quoteExactInputSingle` spot call; allowing configurable/tighter slippage bounds; or detecting abnormal price deviation from a recent reference and deferring/aborting the auto-swap (routing to a manual claim path) rather than executing it under duress.

### Proof of Concept
1. Attacker identifies a pending `GAS_AND_PAYLOAD` inbound (visible via `PendingInbounds`) that will trigger a PRC20→WPC auto-swap once quorum is reached.
2. Attacker swaps a large amount into/out of the PRC20/WPC Uniswap V3 pool in the block immediately preceding inbound finalization, shifting the spot price.
3. `MsgVoteInbound` reaches quorum; `ExecuteInboundGasAndPayload` → `gasAndPayloadDepositAutoSwap` fetches the now-skewed quote via `GetSwapQuote` [5](#0-4)  and executes `CallPRC20DepositAutoSwap` with `minPCOut` computed from that skewed quote.
4. Victim's UEA receives fewer WPC tokens than a fair-price swap would have produced.
5. Attacker reverses the pool-skewing swap, capturing the arbitrage profit extracted from the victim's forced conversion.

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L542-593)
```go
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
