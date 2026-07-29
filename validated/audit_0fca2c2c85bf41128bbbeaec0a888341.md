## Title
Single-block spot-price Uniswap V3 quote used for PRC20→PC auto-swap minting and gas-refund swaps enables sandwich/manipulation extraction of protocol-held PC liquidity - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report describes how relying on a single, easily-outpaced price oracle (Uniswap v2 TWAP for UDT) let an attacker mint/extract value at a price that no longer matched reality. Push Chain's `x/uexecutor` module has a structurally similar dependency: every PRC20→PC conversion (auto-swap on `GAS`/`GAS_AND_PAYLOAD` inbound deposits, and the excess-gas refund path) prices the conversion using a single, unprotected, single-block spot-price read from the Push-chain-deployed Uniswap V3 pool (`QuoterV2.quoteExactInputSingle`), and then derives its own slippage bound from that same manipulable quote.

### Finding Description
`Keeper.GetSwapQuote` in [1](#0-0)  performs a live, non-committed call to the on-chain Uniswap V3 `QuoterV2` contract to get the current spot-price-derived output amount for a `prc20 -> WPC` swap. There is no TWAP, no external price check, and no deviation guard against a second reference price.

This quote is consumed in two production value-transfer flows:

1. **Inbound `GAS`/`GAS_AND_PAYLOAD` deposit auto-swap** — [2](#0-1)  fetches `quote` via `GetSwapQuote` and computes `minPCOut = quote * 95 / 100`, then immediately calls `CallPRC20DepositAutoSwap`, which performs the actual swap on the same pool. The amount of PC ultimately minted/delivered to the user's UEA is whatever the pool's spot price yields at execution time, bounded only by a 5% slippage band computed from the very same spot price.

2. **Excess gas refund with swap** — `applyGasRefund` / `getSwapQuoteForRefund` in [3](#0-2)  and [4](#0-3)  do the identical pattern: quote the gas-token→WPC swap, derive `minPCOut` from that quote, then execute `CallUniversalCoreRefundUnusedGas` with `withSwap=true`.

Because the swap-router/quoter/factory pool referenced here (`UniswapV3Factory`/`SwapRouter`/`QuoterV2`, wired via `UniversalCore.initialize`, see [5](#0-4)  and the e2e AMM deployment in [6](#0-5) ) is a standard, permissionless Uniswap V3-style pool on Push Chain, any unprivileged EVM user can swap against it and move its spot price. An attacker who is also the depositor on the source chain can:

- Push the pool's `prc20-gas-token / WPC` price up (buy WPC with the gas token, or vice versa depending on direction) shortly before their own inbound `GAS` deposit is voted-in and executed by Universal Validators, or
- Time an outbound observation vote (which they cannot directly control, but can influence indirectly by choosing when to trigger execution and by front-running the eventual executing transaction) to land while the pool is in a manipulated state.

Because the module's own `minPCOut` slippage guard is derived from the same manipulated quote (not an independent, harder-to-move reference price), the slippage check provides no protection against pre-existing price manipulation — only against price movement that happens strictly between the quote call and the swap call within the same keeper invocation (which is effectively zero, since they execute back-to-back in the same tx). The real protection gap is upstream: the pool state itself can be pushed away from fair value before the module ever reads it, and the module has no way to detect or reject a manipulated price.

### Impact Explanation
An attacker who manipulates the `prc20/WPC` pool price before a `GAS`/`GAS_AND_PAYLOAD` inbound deposit or an outbound gas refund is priced can cause the module to mint/deliver an inflated amount of PC to themselves relative to the honest market price, funded out of the pool's WPC reserves (and indirectly out of protocol-controlled liquidity). This is a direct value-extraction / fund-drain vector reachable purely through ordinary, unprivileged user actions — sending a cross-chain `GAS` deposit and trading on a public AMM pool — matching the in-scope "corruption of ... gas fee accounting, refund accounting ... misroute value" and "unauthorized ... unauthorized mint" impact categories.

### Likelihood Explanation
Requires the attacker to have capital to temporarily move the specific `prc20-gas-token/WPC` pool and to time it relative to inbound/outbound finalization, similar caveats as the original UDT report (capital and timing requirements bound realistic profit). No validator or admin collusion is required — it only depends on public AMM liquidity depth and normal deposit/vote timing, both of which are attacker-observable.

### Recommendation
- Do not use a single spot-price quoter call as both the pricing source and the basis for its own slippage bound. Add an independent reference (e.g., a TWAP oracle over the same pool, or a comparison against `UniversalCore`'s already-tracked `ChainMeta`/gas-price oracle) and reject/clamp swaps whose spot price deviates materially from the reference.
- Consider enforcing a maximum per-block or per-swap price-impact cap on the auto-swap and gas-refund-swap paths, or route these conversions through a protocol-owned fixed-rate mechanism instead of a public AMM spot price where feasible.
- Add monitoring/alerts for outlier `GetSwapQuote` results relative to recent historical values for the same token pair.

### Proof of Concept
1. Attacker deploys or identifies the Push Chain `prc20-gas-token/WPC` Uniswap V3 pool used by `UniversalCore` (referenced via `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`).
2. Attacker submits a large swap that skews the pool's spot price so that `quoteExactInputSingle(prc20-gas-token -> WPC)` returns an inflated `amountOut`.
3. Attacker initiates a `GAS` inbound deposit from a source chain in the token that maps to this `prc20-gas-token`.
4. When Universal Validators vote the inbound to finalization, `ExecuteInboundGas` calls `GetSwapQuote` (now reading the manipulated price) then `CallPRC20DepositAutoSwap` with `minPCOut` derived from that same manipulated quote — code path: [2](#0-1) .
5. The attacker's UEA receives an inflated PC amount funded from the pool's WPC reserves; attacker then reverses their initial swap (or lets arbitrageurs correct the pool) and nets a profit funded by pool liquidity, analogous to the UDT arbitrage described in the source report.

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

**File:** test/utils/contracts_setup.go (L82-102)
```go
	const (
		WPCAddress              = "0x1111111111111111111111111111111111111111"
		UniswapV3FactoryAddress = "0x2222222222222222222222222222222222222222"
		UniswapV3RouterAddress  = "0x3333333333333333333333333333333333333333"
		UniswapV3QuoterAddress  = "0x4444444444444444444444444444444444444444"
	)

	// Set UEA proxy implementation
	_, err := app.EVMKeeper.CallEVM(
		ctx,
		handlerABI,
		owner,
		handlerAddr,
		true,
		nil,
		"initialize",
		common.HexToAddress(WPCAddress),
		common.HexToAddress(UniswapV3FactoryAddress),
		common.HexToAddress(UniswapV3RouterAddress),
		common.HexToAddress(UniswapV3QuoterAddress),
	)
```

**File:** e2e-tests/setup.sh (L3991-4032)
```shellscript
  local wpc_addr
  wpc_addr="$(find_first_address_with_keywords "$wpc_log" wpc wpush wrapped)"
  if [[ -n "$wpc_addr" ]]; then
    record_contract "WPC" "$wpc_addr"
  else
    log_warn "Could not auto-detect WPC address from logs"
  fi

  local core_log="$LOG_DIR/swap_core_$(date +%Y%m%d_%H%M%S).log"
  log_info "Deploying v3-core"
  (
    cd "$SWAP_AMM_DIR/v3-core"
    npx hardhat compile
    npx hardhat run scripts/deploy-core.js --network pushchain
  ) 2>&1 | tee "$core_log"

  local factory_addr
  factory_addr="$(grep -E 'Factory Address|FACTORY_ADDRESS=' "$core_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  if [[ -n "$factory_addr" ]]; then
    record_contract "Factory" "$factory_addr"
  else
    log_warn "Could not auto-detect Factory address from logs"
  fi

  local periphery_log="$LOG_DIR/swap_periphery_$(date +%Y%m%d_%H%M%S).log"
  log_info "Deploying v3-periphery"
  (
    cd "$SWAP_AMM_DIR/v3-periphery"
    npx hardhat compile
    npx hardhat run scripts/deploy-periphery.js --network pushchain
  ) 2>&1 | tee "$periphery_log"

  local swap_router quoter_v2 position_manager
  swap_router="$(grep -E 'SwapRouter' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  quoter_v2="$(grep -E 'QuoterV2' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  position_manager="$(grep -E 'PositionManager' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  wpc_addr="$(grep -E '^.*WPC:' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"

  [[ -n "$swap_router" ]] && record_contract "SwapRouter" "$swap_router"
  [[ -n "$quoter_v2" ]] && record_contract "QuoterV2" "$quoter_v2"
  [[ -n "$position_manager" ]] && record_contract "PositionManager" "$position_manager"
  [[ -n "$wpc_addr" ]] && record_contract "WPC" "$wpc_addr"
```
