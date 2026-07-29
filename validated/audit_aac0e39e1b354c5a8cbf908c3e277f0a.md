I have enough evidence to write the analog finding now.

### Title
Auto-swap deposit/refund paths derive slippage protection (`minPCOut`) from the same instantaneous spot price they are meant to protect against, enabling AMM sandwich extraction of bridged/relayer funds - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
Multiple execution paths in `x/uexecutor` swap a bridged PRC20 gas token for WPC through the node's own on-chain Uniswap-V3-style pool. In each case, the "slippage protection" bound (`minPCOut`) is computed as 95% of a spot quote (`GetSwapQuote`) taken from the *same* manipulable pool that the swap itself will execute against, immediately before submitting the swap in the same call. This is architecturally identical to CurveOracle's `getPrice` bug: a value read from a manipulable AMM is used both as the "market price" and as its own safety guard, so the guard offers no protection against price manipulation of the underlying pool.

### Finding Description
`GetSwapQuote` [1](#0-0)  calls `QuoterV2.quoteExactInputSingle` on the chain's own WPC⇄PRC20 Uniswap V3 pool (deployed and seeded during setup, e.g. `create-pool`) to obtain a spot-based expected output for a `PRC20 → WPC` swap. Three call sites use this quote to derive `minPCOut = quote * 95 / 100` and then immediately execute the real swap against that identical pool:

- `ExecuteInboundGas` (inbound `GAS` handling): fetches quote then calls `CallPRC20DepositAutoSwap` with the derived `minPCOut` [2](#0-1) 
- `gasAndPayloadDepositAutoSwap` (inbound `GAS_AND_PAYLOAD` handling): identical pattern [3](#0-2) 
- `applyGasRefund` (outbound excess-gas refund swap): identical pattern via `getSwapQuoteForRefund` [4](#0-3) 

Because the pool is a standard permissionless Uniswap-V3-style AMM (WPC/token pools created during setup for every synthetic token) [5](#0-4) , and `defaultFeeTier`/pool addresses are fixed per-token [6](#0-5) , any unprivileged actor can move the pool's instantaneous price by swapping into it directly (e.g. via `SwapRouter`) immediately before their own inbound/outbound is processed. Because the "reference price" (`quote`) and its "safety bound" (`minPCOut`) are both derived from that same, attacker-just-manipulated pool state at execution time, the 5% band constrains deviation from the manipulated price, not from a fair/TWAP price. The attacker can push the pool price in their favor, have their own deposit-auto-swap or refund-swap execute at the inflated rate (since `quote`/`minPCOut` are computed against the same skewed state and always satisfied), and then reverse the price move, extracting value from the pool's liquidity providers/protocol — the same "flash-loan-manipulable spot-price-as-its-own-guard" defect described in the CurveOracle report, except here the exploited price feed is the protocol's own Uniswap V3 pool rather than a Curve LP virtual price.

### Impact Explanation
An unprivileged attacker who can move the WPC/PRC20 pool price (any account with capital, permissionless swap) can extract value from pool liquidity through self-sandwiching around their own bridging (`GAS`/`GAS_AND_PAYLOAD` inbound) or gas-refund (outbound) transactions, since the module never compares the quote against an independent/TWAP reference — only against itself. This directly corrupts PRC20/native asset accounting and gas-refund accounting (an in-scope impact: "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting").

### Likelihood Explanation
Requires no privileged access — any user can supply capital to swap in the permissionless AMM pool immediately before triggering (or observing) their own inbound/outbound processing, and the module's swap execution always follows shortly after within normal protocol flow (finalized ballot → execution, or outbound observation → refund). The 5%-band computed off the same skewed spot price does not block this; it is trivially satisfied by construction.

### Recommendation
Do not derive the slippage-protection bound from the same instantaneous quote used to size the swap. Use a time-weighted average price (or an external/independent oracle) for the reference price, or bound `minPCOut` against a governance-configured/registry-stored expected exchange rate rather than a live `quoteExactInputSingle` call taken microseconds before execution. Consider capping per-block price impact or routing these system swaps through pools with circuit breakers.

### Proof of Concept
1. Attacker holds `PRC20_X` and native funds; a public `PRC20_X/WPC` Uniswap V3 pool exists (as created by `create-pool`/`configureUniversalCore`).
2. Attacker submits (or has pending) an inbound `GAS_AND_PAYLOAD` deposit of `PRC20_X`, which the finalized ballot will route through `gasAndPayloadDepositAutoSwap`.
3. Immediately before the validator set executes/finalizes that inbound, attacker swaps a large amount of `WPC → PRC20_X` in the same pool via the public `SwapRouter`, depressing the `PRC20_X` price (raising `PRC20_X`→`WPC` output rate).
4. `GetSwapQuote` now returns an inflated `amountOut` for the attacker's deposit swap; `minPCOut = quote*95/100` is likewise inflated but still satisfied because the real swap executes against the same skewed pool.
5. Attacker's deposit-auto-swap mints an outsized amount of WPC relative to fair value, extracted from the pool's liquidity providers/protocol; attacker then reverses their initial `WPC→PRC20_X` swap to restore price and realize profit. [1](#0-0) [7](#0-6)

### Citations

**File:** x/uexecutor/keeper/evm.go (L470-498)
```go
// GetDefaultFeeTierForToken reads defaultFeeTier[prc20] from UniversalCore.
func (k Keeper) GetDefaultFeeTierForToken(ctx sdk.Context, prc20Address common.Address) (*big.Int, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "defaultFeeTier", prc20Address)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call defaultFeeTier")
	}

	results, err := abi.Methods["defaultFeeTier"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack defaultFeeTier result")
	}

	// go-ethereum unpacks uint24 as *big.Int (non-standard widths always map to *big.Int)
	fee, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for defaultFeeTier: %T", results[0])
	}

	return fee, nil
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

**File:** e2e-tests/setup.sh (L4551-4569)
```shellscript
  while IFS=$'\t' read -r token_symbol token_addr; do
    [[ -n "$token_addr" ]] || continue
    if [[ "$(echo "$token_addr" | tr '[:upper:]' '[:lower:]')" == "$(echo "$wpc_addr" | tr '[:upper:]' '[:lower:]')" ]]; then
      continue
    fi

    local pool_token_amount="1"
    local pool_wpc_amount="4"
    if [[ "$token_symbol" == "pSOL" ]]; then
      pool_token_amount="${LOCAL_PSOL_POOL_TOKEN_AMOUNT:-50}"
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
    (
      cd "$SWAP_AMM_DIR"
      node scripts/pool-manager.js create-pool "$token_addr" "$wpc_addr" 4 500 true "$pool_token_amount" "$pool_wpc_amount"
    )
  done < <(jq -r '.tokens[]? | [.symbol, .address] | @tsv' "$DEPLOY_ADDRESSES_FILE")
```
