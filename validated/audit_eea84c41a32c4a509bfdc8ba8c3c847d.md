## Title
Fixed 5% slippage tolerance on module-driven PRC20→PC auto-swaps enables MEV sandwich extraction from GAS-type inbound deposits and outbound gas refunds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report's core lesson is that a claim amount computed against a shared, order-dependent value (an AMM/pool state) without a caller-controlled minimum-output check lets a party who can influence transaction ordering capture value that would otherwise go to the honest party. Push Chain's `x/uexecutor` module reproduces the same class of bug in its Uniswap V3 auto-swap paths: the module quotes a swap and then executes it with a hardcoded `minPCOut = quote * 95 / 100`, i.e. a fixed 5% slippage tolerance that is *not* set by, or protective of, the actual depositing/refunding user. Because the quote-then-swap sequence executes atomically inside a validator vote transaction that is visible before inclusion, an unprivileged attacker can sandwich it and extract value out of the 5% band on every GAS-type inbound deposit and every outbound gas refund.

### Finding Description
Three call sites compute a swap quote and then immediately submit the swap with a fixed 5%-tolerance floor:

- `ExecuteInboundGas` (GAS inbound path): [1](#0-0) 

- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound path): [2](#0-1) 

- `applyGasRefund` (successful/failed outbound gas refund path): [3](#0-2) 

All three fetch a live quote via `GetSwapQuote` (a `CallEVM` to `QuoterV2.quoteExactInputSingle`) and then commit the swap through `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` with `minPCOut` derived only as `quote * 95 / 100`: [4](#0-3) [5](#0-4) 

This is exactly the shape of the reported bug class: the "claim amount" (here, the PC output of the swap credited to the user's UEA, or the PC refunded to a relayer/sender) is computed against a mutable shared resource (the AMM pool's price) at execution time, and the only protection is a static, generous 5% band rather than a value that reflects the user's actual expectation. The confirmed-upgrade note explicitly documents this as a deliberate replacement of a "0-slippage" call with the 95%-of-quote floor: [6](#0-5) 

The trigger for the swap (validator vote reaching quorum on `MsgVoteInbound`/`MsgVoteOutbound`) is a normal, user-visible Cosmos transaction submitted through the public mempool — there is no evidence in this repository of a private-mempool/sequencer guarantee analogous to the one that led judges to discount severity in the original report. An unprivileged attacker who observes the pending vote transaction that will trip quorum (and thus trigger the deposit/refund auto-swap) can:
1. Front-run with a large swap in the same Uniswap V3 pool (`prc20 -> WPC`) to push the price against the module's incoming trade, up to the edge the 5% tolerance still allows the module's swap to succeed.
2. Let the module's `depositPRC20WithAutoSwap` / `refundUnusedGas` swap execute at the manipulated (worse) price, but still above `minPCOut` because the bound is a fixed 5% of a quote fetched only moments earlier at the manipulated price context, not a pre-manipulation baseline.
3. Back-run to restore the price, pocketing the difference.

Every user who bridges gas top-ups (`GAS`, `GAS_AND_PAYLOAD` inbounds) or is owed an excess-gas refund on outbound observation loses up to ~5% of the PC they should have received, since the module has no user-supplied slippage input and instead applies the same static, sandwich-friendly tolerance unconditionally.

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting… or canonical UniversalTx state" and "draining… of user or protocol-controlled funds" in the allowed-impact gate. The loss is systematic and repeatable — every inbound `GAS`/`GAS_AND_PAYLOAD` deposit and every outbound gas refund routes through the same fixed-tolerance auto-swap, so an attacker can extract value from the pool on essentially every occurrence rather than a one-off race condition. Because 5% is materially larger than a normal DEX slippage tolerance (typically 0.1%–1%), the extractable value per transaction can be significant relative to deposit size.

### Likelihood Explanation
The trigger condition (an unprivileged attacker observing a pending validator vote transaction and front/back-running the associated AMM swap) requires only a public mempool and liquidity in the relevant Uniswap V3 pool — no validator or admin compromise is needed, consistent with the "unprivileged external attacker" and "honest validators/honest nodes" constraints of the allowed-impact gate. The main uncertainty is whether Push Chain's actual mempool/sequencing setup exposes these vote transactions before inclusion (as with the original report, this materially affects real-world exploitability); this repository does not evidence a private-mempool guarantee.

### Recommendation
- Do not derive `minPCOut` solely from a same-call quote with a static percentage; consider a TWAP-based reference price, a much tighter tolerance, or route swaps through commit-reveal / batched execution to reduce sandwichability.
- Where feasible, let the amount at risk be capped or split across multiple smaller swaps to reduce single-block MEV extraction.
- Consider making the deposit/refund auto-swap resistant to same-block manipulation, e.g., by comparing the quote against a moving average oracle price rather than the instantaneous pool price fetched immediately before the swap.

### Proof of Concept
1. Attacker monitors the mempool for the quorum-reaching `MsgVoteInbound` (or `MsgVoteOutbound`) for a `GAS`/`GAS_AND_PAYLOAD` inbound (or an outbound with excess gas fee).
2. Attacker submits a swap in the same `prc20 -> WPC` Uniswap V3 pool (with higher gas/priority) to move the pool price down within the 5% band that `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund` will still accept as `>= minPCOut`.
3. The validator vote transaction lands, triggering `GetSwapQuote` → `minPCOut = quote*95/100` → `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, executing at the manipulated price.
4. Attacker back-runs to restore the pool price, realizing profit equal to the price impact captured within the tolerance band, at the expense of the amount credited to the user's UEA or refunded to the sender/relayer. [7](#0-6) [8](#0-7)

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

**File:** x/uexecutor/keeper/outbound.go (L213-231)
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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
