Confirmed: no TWAP is used anywhere in the codebase — `quoteExactInputSingle` (spot-price QuoterV2) is the only pricing source for gas-token autoswap, and the same spot price is used to both compute the quote and derive `minPCOut`.

### Title
Spot-price Uniswap V3 quote used for gas-token autoswap lets an attacker manipulate PC output received by inbound-gas users - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/evm.go)

### Summary
The `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbound flows convert a user's deposited PRC20 gas token into native PC by calling `depositPRC20WithAutoSwap` on the `UniversalCore` Uniswap V3 pool. The expected output and the slippage floor are both derived from a single live spot-price call (`GetSwapQuote` → `QuoterV2.quoteExactInputSingle`) with no TWAP or independent price reference, so an unprivileged actor who can trade against that same pool can push the price down immediately before the module's autoswap executes and pocket the difference at the depositing user's expense.

### Finding Description
`ExecuteInboundGas` ( [1](#0-0) ) and `gasAndPayloadDepositAutoSwap` ( [2](#0-1) ) compute the swap output as follows:
1. `GetDefaultFeeTierForToken` reads the configured fee tier for the pool.
2. `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` on-chain to get `amountOut` for the deposited `amount`, using the pool's *current* spot state (`SqrtPriceLimitX96: 0`), as seen in [3](#0-2) .
3. `minPCOut` is derived from that same quote with a flat 5% haircut: `minPCOut = quote * 95 / 100`.
4. `CallPRC20DepositAutoSwap` immediately executes the real swap against the pool using that `minPCOut` as the only floor, at [4](#0-3) .

Because the "slippage protection" floor is computed from the *same* spot price that is about to be used for the real swap, it defends only against price movement between step 2 and step 4 (which is negligible — both happen back-to-back inside one deterministic keeper call). It provides no protection against the pool price having already been pushed away from fair value by an attacker's own prior swap. Any unprivileged user can submit an ordinary swap transaction against the `UniversalCore` Uniswap V3 pool to move the spot price before the validator-quorum-triggered `MsgVoteInbound` that runs this auto-swap logic is processed in the same or a nearby block, then reverse the trade afterward (classic sandwich). The same unmitigated spot-price pattern also drives the gas-refund swap path in `applyGasRefund`/`getSwapQuoteForRefund` ( [5](#0-4) ).

This matches the bug class in the external report — a swap-rate calculation that an attacker can time/skew so that a victim's deposit converts at a manipulated rate — except here the manipulated variable is the AMM pool's spot price rather than the vault's credit capacity, and the corrupted value is the amount of native PC minted into the recipient's UEA/EOA via `depositPRC20WithAutoSwap` for `GAS` and `GAS_AND_PAYLOAD` inbound transactions.

### Impact Explanation
A successful sandwich around the auto-swap step results in the depositing user's UEA receiving up to ~5% less PC than fair value for their deposited gas token (bounded by the `minPCOut` haircut per single swap, but repeatable across every `GAS`/`GAS_AND_PAYLOAD` inbound and across the refund path), while the attacker profits the corresponding amount by trading against the same pool. This is a permanent loss of protocol/user-controlled funds and a corruption of the native-asset/gas-token accounting recorded in `UniversalTx.PcTx`, matching the in-scope impact "corruption of ... gas fee accounting ... token mapping" and "permanent loss ... of user or protocol-controlled funds," triggered purely by an unprivileged actor's own ordinary swap transactions with no validator, relayer, or admin collusion required.

### Likelihood Explanation
Likelihood depends on pool liquidity depth for each configured gas-token/WPC pair and on the attacker's ability to get a swap transaction ordered immediately before the `MsgVoteInbound` tx that triggers the auto-swap within the same block (a function of mempool/proposer behavior, not of any special privilege). For thinly-liquidity pools this is cheap and repeatable for every gas-abstraction inbound processed, making it a realistic, continuously exploitable griefing/extraction vector rather than a one-off edge case.

### Recommendation
Do not derive the slippage floor from the same spot-price call that immediately executes the swap. Use a manipulation-resistant reference price (e.g., a time-weighted average price over multiple blocks, or an external price oracle) to compute `minPCOut`, and/or require the quote to be supplied and bounded by governance/registry-configured price bands rather than a single live `quoteExactInputSingle` call taken immediately before the swap.

### Proof of Concept
1. Attacker observes an in-flight inbound `MsgVoteInbound` (visible in mempool/observed cross-chain event) that will trigger `ExecuteInboundGas` for a large gas-token deposit.
2. Attacker submits (and gets included ahead of the quorum tx in the same or preceding block) a large swap on the `UniversalCore` Uniswap V3 pool for `prc20 → WPC`, pushing the spot price down.
3. When the quorum-reaching `MsgVoteInbound` executes, `GetSwapQuote`/`CallPRC20DepositAutoSwap` ( [1](#0-0) ) reads the manipulated spot price, computes `minPCOut` from it, and executes the swap — the depositing user's UEA receives materially less PC than fair value.
4. Attacker reverses the initial swap (`WPC → prc20`) to restore price and realize the arbitrage profit extracted from the victim's deposit.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
```

**File:** x/uexecutor/keeper/evm.go (L500-537)
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

**File:** x/uexecutor/keeper/outbound.go (L174-269)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

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

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
}

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
```
