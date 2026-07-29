## Analysis

The Sushi JIT-liquidity bug class is about **exploiting a spot-computed value (position.liquidity) that feeds directly into an on-chain payout, by manipulating that spot value for a single transaction and reverting afterward** — i.e., a same-block sandwich against something the protocol trusts as an instantaneous truth.

In Push Chain's scoped `x/uexecutor` code, the direct analog is the **spot AMM quote used to bound the gas-refund and auto-swap flows**: `GetSwapQuote` (calling the Uniswap-style `QuoterV2.quoteExactInputSingle` on-chain, in the same execution as the swap) feeds a `minPCOut = quote * 95 / 100` slippage bound that is then used for a real swap of protocol/user-controlled funds. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Same-block spot-quote slippage bound in PRC20 auto-swap and gas-refund swap enables sandwich extraction of user/protocol funds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund` each fetch a live on-chain quote from the Uniswap-style `QuoterV2` immediately before executing a swap of PRC20 → WPC/PC on behalf of a user or the protocol, using a fixed `95%` slippage tolerance (`minPCOut = quote * 95 / 100`) computed and consumed within the same keeper call.

### Finding Description
Three flows compute a quote and execute the corresponding swap back-to-back with no time or price decoupling:
- `ExecuteInboundGas` (inbound gas-abstraction swap for `FUNDS_AND_PAYLOAD`/gas-only inbounds): `GetSwapQuote` → `minPCOut` → `CallPRC20DepositAutoSwap`.
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbounds): same pattern.
- `applyGasRefund` (outbound gas refund): `getSwapQuoteForRefund` → `minPCOut` → `CallUniversalCoreRefundUnusedGas` with `withSwap=true`.

Because the quote is read from the pool's current reserves at execution time rather than from an external/time-weighted price oracle, and the tolerated deviation is a generous flat `5%`, an attacker who can predict or observe (via mempool visibility of the gasless `MsgVoteInbound`/`MsgVoteOutbound` transactions that trigger these swaps) when the swap will execute can push the pool price by more than 5% immediately beforehand (a large swap or JIT-minted concentrated liquidity removal, exactly the pattern in the cited report) and revert it immediately after. The protocol's swap then executes at the manipulated price, still satisfying the `minPCOut` check, and the attacker captures the difference. Unlike a genuine market-driven slippage event, this is an attacker-manufactured, single-block, reversible price movement — the same "manufacture a very large but transient position/value, extract the payout computed against it, then withdraw" structure as the Sushi Trident JIT liquidity report.

Amounts are attacker-influenced: the swapped `amount` for the auto-swap path derives from `inbound.Amount`, which the attacker fully controls as the source-chain depositor, and the pools involved are per-token pools that per `uregistry` are configured with a `liquidity cap` per token config, i.e., they can be thin and cheap to move. [4](#0-3) [5](#0-4) 

### Impact Explanation
This directly maps to "corruption of ... gas fee accounting, refund accounting, ... token mapping" and "misroute value" under the Push Chain allowed-impact gate: value that should accrue to the depositing user (auto-swap output) or the refund recipient (excess gas refund) is instead partially captured by an unprivileged attacker manipulating the swap pool, at the expense of user/protocol-controlled PRC20/PC funds. Because the amount and timing of inbound deposits are attacker-controlled, and refund swaps fire deterministically off UV vote finalization (observable pre-commit), this is systematically repeatable rather than a one-off griefing event.

### Likelihood Explanation
Moderate. It requires: (a) a pool with limited liquidity relative to the swapped amount (plausible given per-token liquidity caps), (b) the ability to see the finalizing vote transaction before it lands (gasless UV votes are ordinary mempool transactions, not shielded), and (c) capital to move the price >5% and reverse it, which is inexpensive if done via flash-swap/flash-loan style bracketing since no funds are permanently at risk (mirroring the disputed Sushi finding, which was acknowledged as a real MEV pattern even though the reporter's proposed "huge IL" framing didn't apply here since PC's swaps aren't LP-fee-bearing positions the attacker holds).

### Recommendation
- Replace the same-block spot `QuoterV2` quote with a manipulation-resistant reference (TWAP over multiple blocks, or an external price oracle) for computing `minPCOut`.
- Tighten the slippage tolerance from a flat 5% and/or make it configurable per token liquidity depth.
- Consider requiring the quote and the swap execution to be separated by at least one block, or cap the swap size relative to observed pool depth.

### Proof of Concept
1. Attacker identifies a PRC20/WPC pool with configured liquidity cap X that is thin relative to a plausible inbound deposit amount.
2. Attacker submits a source-chain deposit (inbound) of an amount close to the pool's tolerable size, destined to trigger `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`.
3. While UVs are voting (or right before broadcasting the finalizing `MsgVoteInbound`, visible in mempool), attacker swaps a large amount into/out of the same pool to push price outside the true value by >5%.
4. The module's `GetSwapQuote` (read at the same manipulated moment) returns a skewed quote; `minPCOut` (95% of skewed quote) is still satisfied when `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes.
5. Attacker reverses their manipulating trade in the same or next block, net-capturing the value difference that should have gone to the depositing user (or refund recipient), extracted from the pool/protocol-controlled liquidity. [6](#0-5) [7](#0-6)

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L46-52)
```go
		// --- step 2: parse amount
		amount := new(big.Int)
		if amount, ok := amount.SetString(inbound.Amount, 10); !ok {
			execErr = fmt.Errorf("invalid amount: %s", inbound.Amount)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
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

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
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

**File:** x/uregistry/README.md (L99-101)
```markdown
x/uregistry/
|-- keeper/
|   |-- keeper.go              State, lookups, system-contract deployment
```
