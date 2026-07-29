## Title
Unnecessary same-token (WPC→WPC) AutoSwap fee charged on gas-abstraction deposits and gas refunds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The WooFi report describes a case where a cross-chain router unconditionally routes a bridged asset through a swap even when the source and destination assets are effectively the same (sgETH→WETH), causing the pool to execute a same-token swap and charge an avoidable fee. Push Chain's gas-abstraction and gas-refund flows have the same structural defect: they call the Uniswap-style auto-swap path (`depositPRC20WithAutoSwap` / `refundUnusedGas` with `withSwap=true`) unconditionally, without ever checking whether the PRC20/gas token being swapped is already the WPC (wrapped PC) token itself.

### Finding Description
In `ExecuteInboundGas` [1](#0-0) , the keeper unconditionally fetches `wpcAddr` via `GetUniversalCoreWPCAddress`, then calls `GetSwapQuote(quoterAddr, prc20AddressHex, wpcAddr, fee, amount)` and `CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)` with no check that `prc20AddressHex != wpcAddr`. The same unconditional pattern exists in `gasAndPayloadDepositAutoSwap` [2](#0-1) , which is used for `GAS_AND_PAYLOAD` inbounds.

The mirrored refund path, `applyGasRefund` in `outbound.go`, does the same thing on the way back out: it calls `getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)` (which always quotes `gasToken → wpcAddr`) and then `CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)` with `withSwap=true`, again without checking whether `gasToken` already equals WPC [3](#0-2) [4](#0-3) .

`prc20AddressHex` / `gasToken` are derived directly from the registry's `TokenConfig.NativeRepresentation.ContractAddress` for the chain+asset pair involved [5](#0-4) . If a chain's registered gas-token PRC20 representation is set to the WPC contract itself (the natural registration for PC's own token when it round-trips through an external chain and is bridged back as a gas top-up, or for any token whose native representation is configured as WPC), every gas-abstraction deposit or unused-gas refund for that token will run a `WPC → WPC` swap through `UniversalCore`'s Uniswap V3-style pool, incurring the pool's `defaultFeeTier` fee for no economic reason — mirroring exactly the WooFi `WETH → WETH` scenario.

### Impact Explanation
Every ordinary user who bridges gas using such a token pays an avoidable Uniswap fee (whatever `defaultFeeTier` is configured for that PRC20) on both the inbound gas-abstraction leg (`CallPRC20DepositAutoSwap`) and the outbound unused-gas refund leg (`CallUniversalCoreRefundUnusedGas` with `withSwap=true`). This is a direct, unauthorized reduction of the amount of PC the user's UEA receives relative to what they funded/expect — a corruption of gas-fee/refund accounting reachable purely through normal inbound/outbound gas flows, matching the in-scope impact class "corruption of ... gas fee accounting, refund accounting ... token mapping."

### Likelihood Explanation
No malicious actor is required — this triggers whenever an honestly-configured token's `NativeRepresentation.ContractAddress` happens to equal WPC and a normal user submits a `GAS` or `GAS_AND_PAYLOAD` inbound, or an outbound refund is computed for that gas token. The code paths (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, `applyGasRefund`) always execute the swap leg unconditionally; there is no guard anywhere in `x/uexecutor/keeper` comparing the token address against WPC before invoking the swap.

### Recommendation
In `ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`, compare the resolved PRC20/gas-token address against `wpcAddr` before choosing the swap path. If they match, skip `GetSwapQuote`/`CallPRC20DepositAutoSwap` (use the plain `CallPRC20Deposit` path) and skip the `withSwap=true` branch of `CallUniversalCoreRefundUnusedGas` (call it with `withSwap=false` directly), delivering the token to the recipient/UEA without an unnecessary fee-bearing swap.

### Proof of Concept
1. Register (or observe an existing) `TokenConfig` for some source chain whose `NativeRepresentation.ContractAddress` equals the `WPC` address returned by `UniversalCore.WPC()`.
2. Submit a `GAS` (or `GAS_AND_PAYLOAD`) inbound for that asset; `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` resolves `prc20AddressHex == wpcAddr` and still calls `GetSwapQuote(quoterAddr, prc20AddressHex, wpcAddr, fee, amount)` followed by `CallPRC20DepositAutoSwap`, executing a same-token Uniswap swap and charging `defaultFeeTier` fee [6](#0-5) .
3. Observe the UEA receives less than `amount` due to the pool fee, identical in effect to the WooFi `WETH → WETH` unnecessary-fee scenario.
4. Similarly, when the corresponding outbound is later observed successful with excess gas, `applyGasRefund` performs `gasToken(=WPC) → WPC` swap via `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)`, again charging an avoidable fee on the refund amount [3](#0-2) .

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-54)
```go
	// --- step 1: get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
		// --- step 2: parse amount
		amount := new(big.Int)
		if amount, ok := amount.SetString(inbound.Amount, 10); !ok {
			execErr = fmt.Errorf("invalid amount: %s", inbound.Amount)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
			// --- step 3: resolve / deploy UEA
			prc20AddressHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
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

**File:** x/uexecutor/keeper/outbound.go (L213-235)
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
	} else {
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
