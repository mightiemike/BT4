## Finding: Hardcoded 5% Swap Slippage Tolerance in Gas-Abstraction Auto-Swaps Enables MEV Sandwich Extraction

The reported Aave issue (hardcoded, non-configurable `TOLERANCE_BIPS` at an overly loose 2% instead of 0.5%) has a direct, and in some ways more severe, analog in Push Chain's gas-abstraction auto-swap logic.

### Title
Hardcoded 5% slippage tolerance on Uniswap V3 auto-swaps exposes gas-abstraction deposits/refunds to systematic MEV sandwich extraction - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every GAS / GAS_AND_PAYLOAD inbound deposit-with-autoswap, and every outbound excess-gas refund swap, computes `minPCOut` as a hardcoded `quote * 95 / 100` (5% slippage tolerance) with no configurability, no admin/governance parameter, and no cap on the acceptable deviation.

### Finding Description
`ExecuteInboundGas` fetches a Uniswap V3 `QuoterV2` quote and then unconditionally applies a 5% slippage tolerance before calling `CallPRC20DepositAutoSwap`: [1](#0-0) 

The same pattern is duplicated verbatim in the GAS_AND_PAYLOAD helper: [2](#0-1) 

And again in the outbound excess-gas refund path (`applyGasRefund` / `getSwapQuoteForRefund`): [3](#0-2) 

In all three sites, the value `95`/`100` is an inline magic-number literal — not even a named module constant, let alone a governance-settable `Params` field the way `x/uregistry` and other modules expose (`MsgUpdateParams`). The quote is fetched via `GetSwapQuote` (a view `CallEVM`) and the actual swap is committed moments later in the same keeper flow via `DerivedEVMCall` with `commit=true`: [4](#0-3) [5](#0-4) 

Because this quote-then-swap sequence executes deterministically as part of inbound-ballot finalization (all Universal Validators converge on and execute the same inbound in the same block), and the on-chain pool price used by `quoteExactInputSingle` can be moved by any unprivileged user submitting ordinary swap transactions against the same Uniswap V3 pool in a preceding transaction of the same block, an attacker can:
1. Push the PRC20/WPC pool price against the pending inbound deposit just before the module's `DerivedEVMCall` executes.
2. Let the module accept up to 5% worse execution than the "true" market price (the tolerance is 10x looser than the report's own recommended 0.5% starting point, and the report's original 2% was already flagged as too loose).
3. Reverse the price move in a following transaction, capturing the difference — a classic sandwich, but against a protocol-controlled, deterministically-triggered swap rather than a user-initiated one, which makes the target trivially discoverable (pending inbounds/outbounds are public chain state) and the attack repeatable on every gas-abstraction inbound/refund.

### Impact Explanation
Every user who bridges a non-native gas token into Push Chain (GAS or GAS_AND_PAYLOAD inbound) or receives an excess-gas refund is exposed to guaranteed value leakage of up to 5% per swap, extractable by any unprivileged actor monitoring the mempool/pending inbound queue and trading against the same Uniswap V3 pool. This is systemic drainage of protocol/user-controlled value on a core, high-frequency execution path (`ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, and gas-refund accounting), not a one-off misconfiguration — it fires on every relevant transaction and cannot be mitigated by the recipient since the swap parameters are entirely module-controlled.

### Likelihood Explanation
High. No privileged access is required — any user can trade against the target pool to move price, and the timing of pending inbound/outbound execution is observable on-chain (it depends only on honest-validator ballot finalization of ordinary user-submitted deposits). The 5% band is wide enough to make the attack profitable even accounting for gas costs and typical pool depth for a chain in its early stage with likely lower liquidity.

### Recommendation
- Replace the hardcoded `95`/`100` literal with a governance/admin-configurable slippage parameter (analogous to other modules' `Params.Admin`-gated `MsgUpdateParams`), defaulting to a much tighter tolerance (e.g. 0.5%–1%).
- Consider deriving the tolerance dynamically from a TWAP or a maximum acceptable deviation from a recent oracle price rather than a flat percentage of the instantaneous spot quote, since `GetSwapQuote` itself reads the same spot price that is manipulable.
- Apply the same fix consistently across `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `outbound.go`'s refund path so all three swap sites share one configurable source of truth.

### Proof of Concept
1. Attacker identifies a pending `GAS` inbound (visible via `PendingInbounds`) targeting a PRC20 with a thinly-liquid Uniswap V3 pool against WPC.
2. Attacker submits a large swap in the same pool, moving price against the pending deposit's swap direction.
3. When Universal Validators finalize and execute the inbound, `GetSwapQuote` returns a quote reflecting the attacker-moved price; `minPCOut = quote * 95 / 100` still passes even though the "fair" output is up to 5% higher.
4. `CallPRC20DepositAutoSwap` executes at the manipulated price, crediting the user's UEA with up to 5% less PC than a fair execution would have.
5. Attacker reverses their initial swap, capturing the extracted value, repeatable on every subsequent GAS/GAS_AND_PAYLOAD inbound or gas refund touching the same or similarly shallow pools.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-148)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L374-378)
```go
	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L217-223)
```go
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

**File:** x/uexecutor/keeper/evm.go (L574-593)
```go
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
