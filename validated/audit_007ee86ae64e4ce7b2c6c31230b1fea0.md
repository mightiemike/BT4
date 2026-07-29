I have sufficient evidence to confirm the analog. The pattern is confirmed and matches the report's root cause precisely: the "minimum output" protecting a swap is derived from a live, attacker-influenceable on-chain quote fetched at execution time, rather than a value committed independently before the swap.

### Title
Inbound gas-swap and gas-refund `minPCOut` slippage protection is computed from a runtime `GetSwapQuote` call and can be trivially bypassed by sandwiching, draining user gas-abstraction deposits/refunds - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`GetSwapQuote` calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` [1](#0-0)  to get the current expected output for a PRC20→WPC swap, and this same quote is immediately used to derive the swap's slippage floor (`minPCOut = quote * 95 / 100`) that is passed into `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` [2](#0-1) . Just like the SdtRewardReceiver bug (using `get_dy` to compute `_min_dy` for the same `exchange` call), this baseline reflects the *current, potentially attacker-manipulated* pool state rather than a value fixed independently of the swap's execution context. Adding a flat 5% margin does not fix the underlying design flaw — it only requires the attacker to move the pool price by more than 5%, which is straightforward against a newly-created, thinly-liquidated Uniswap V3 pool (seed liquidity in the e2e setup is as low as `1 token / 4 WPC` [3](#0-2) ).

### Finding Description
Three module-originated swap paths all follow the identical vulnerable pattern:

1. **Inbound `GAS` execution** — `ExecuteInboundGas` fetches `quote := GetSwapQuote(...)` then computes `minPCOut := quote*95/100` and calls `CallPRC20DepositAutoSwap` with it [4](#0-3) .
2. **Inbound `GAS_AND_PAYLOAD` execution** — `gasAndPayloadDepositAutoSwap` does the same quote→minPCOut→swap sequence [5](#0-4) .
3. **Outbound excess-gas refund** — `applyGasRefund`/`getSwapQuoteForRefund` compute `minPCOut` the same way before calling `refundUnusedGas(..., withSwap=true, ...)` [6](#0-5) [7](#0-6) .

In all three cases, `GetSwapQuote` performs a non-committed EVM call to `quoteExactInputSingle` at the moment of execution [8](#0-7) , i.e. it reads whatever pool reserves exist at that exact block. The `minPCOut` derived from it is therefore never an independent, user/protocol-committed expected value — it is a percentage of the manipulated state itself. `CallPRC20DepositAutoSwap` also passes `deadline = 0` ("contract uses its default"), providing no additional protection against delayed execution [9](#0-8) .

These swaps are triggered deterministically by ordinary user-reachable events: an inbound deposit reaching UV vote threshold, or an outbound observation vote reaching threshold, both processed via public, mempool-visible Cosmos transactions (`MsgVoteInbound`/`MsgVoteOutbound`, both gasless and whitelisted per `app/txpolicy/gasless.go`). An unprivileged attacker monitoring the mempool can:
1. Front-run the finalizing vote transaction with a swap on the same PRC20/WPC Uniswap V3 pool that moves the price against the pending deposit/refund direction (e.g., dump WPC to depress `quoteExactInputSingle`'s prc20→WPC output).
2. Let the finalizing transaction execute — `GetSwapQuote` now returns a depressed price, `minPCOut` is set to 95% of that already-bad price, and `depositPRC20WithAutoSwap`/`refundUnusedGas` executes at the bad rate without reverting.
3. Back-run to restore the pool price and pocket the difference.

### Impact Explanation
This directly drains value from ordinary users performing normal gas-abstraction deposits (`GAS`/`GAS_AND_PAYLOAD` inbound flows) and from users owed excess-gas refunds on outbound execution — both are default, unprivileged, user-reachable flows. The attacker captures the spread via sandwiching while the user's UEA receives less PC than they should have, and the excess-refund recipient receives less than owed. This is unauthorized draining/permanent loss of user-controlled funds and corruption of native-asset/gas-refund accounting, matching the in-scope impact categories for universal execution and gas accounting.

### Likelihood Explanation
Likelihood is high: no privileged access, validator collusion, or off-chain oracle compromise is required — only visibility into the Push Chain mempool/EVM state and the ability to submit ordinary swap transactions against the same on-chain PRC20/WPC pool used by `UniversalCore`. Newly bootstrapped pools with shallow liquidity (as configured in the e2e setup, e.g. `1/4` token/WPC ratio) make a >5% price move trivial and cheap.

### Recommendation
Do not derive `minPCOut` from a same-transaction/runtime `GetSwapQuote` call. Instead:
- Allow the depositing/refunding party (or a protocol-configured, time-averaged oracle such as a Uniswap V3 TWAP over a meaningful window) to set/verify an independent minimum output that cannot be manipulated by the same actor executing the swap.
- If a runtime quote must be used as a reference, cross-check it against a TWAP-derived price and reject/queue the swap when they diverge beyond a safe bound, rather than deriving the floor purely from the spot quote.
- Set an explicit, short `deadline` on `depositPRC20WithAutoSwap` rather than `0`.

### Proof of Concept
1. Attacker observes a pending `MsgVoteInbound` (or `MsgVoteOutbound`) transaction in the mempool that will push `ExecuteInboundGas`/`applyGasRefund` over the UV vote threshold, triggering a PRC20→WPC autoswap for a known token/amount.
2. Attacker submits (and gets included ahead of it, e.g. via higher priority/gas or same-block ordering) a large swap against the same Uniswap V3 pool (`prc20AddressHex`/`gasToken` vs `wpcAddr`) to depress the PRC20→WPC exchange rate.
3. The finalizing vote transaction executes: `GetSwapQuote` [1](#0-0)  returns the depressed rate; `minPCOut = quote*95/100` [10](#0-9)  is computed from that depressed value; `CallPRC20DepositAutoSwap` executes and succeeds at the bad rate since the floor was derived from the very state the attacker manipulated.
4. Attacker back-runs with a reverse swap to restore the pool price, realizing the arbitrage extracted from the victim's deposit/refund, while the user's UEA (or refund recipient) receives materially less PC than expected.

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

**File:** x/uexecutor/keeper/evm.go (L585-592)
```go
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

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

**File:** e2e-tests/setup.sh (L4556-4564)
```shellscript

    local pool_token_amount="1"
    local pool_wpc_amount="4"
    if [[ "$token_symbol" == "pSOL" ]]; then
      pool_token_amount="${LOCAL_PSOL_POOL_TOKEN_AMOUNT:-50}"
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
```

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
