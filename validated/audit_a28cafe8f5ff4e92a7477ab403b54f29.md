This confirms the analog: `GetSwapQuote` fetches a live on-chain price at the exact block the inbound ballot happens to finalize, and `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` derive `minPCOut` from that quote with a hardcoded 95% (5% slippage) factor that is baked into keeper code, not something the depositing user ever chose, and the deadline is passed as `big.NewInt(0)` — i.e. unset/no expiry. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Auto-swap price protection on GAS/GAS_AND_PAYLOAD deposit and gas-refund flows uses a hardcoded 5% slippage tolerance and no swap deadline instead of a caller-specified acceptable range - (File: x/uexecutor/keeper/evm.go, execute_inbound_gas.go, execute_inbound_gas_and_payload.go, outbound.go)

### Summary
This is the direct analog of the referenced bug class: the referenced report criticizes protocols that infer an execution price contextually (from current pool/mark state) at the moment of execution rather than letting the initiating party bound the acceptable price range and set an expiry. Push Chain's `x/uexecutor` module has the exact same pattern in its Uniswap-V3-based PRC20↔WPC auto-swap paths used for `GAS`/`GAS_AND_PAYLOAD` inbound deposits and for excess-gas refunds on outbound finalization.

### Finding Description
When an inbound of type `GAS` or `GAS_AND_PAYLOAD` is finalized (which only happens once a UV ballot reaches 2/3 quorum, an event whose timing is outside the depositor's control), the keeper fetches a live quote via `GetSwapQuote` (Uniswap V3 `QuoterV2.quoteExactInputSingle`) and computes `minPCOut = quote * 95 / 100` — a slippage bound that is hardcoded in Go, not supplied by the user who initiated the deposit on the source chain. [1](#0-0)  The same pattern recurs in `gasAndPayloadDepositAutoSwap` and in `applyGasRefund`'s swap-refund leg. [5](#0-4) 

The resulting swap call, `depositPRC20WithAutoSwap`, is invoked with `deadline = big.NewInt(0)`, meaning "no expiry" is passed at the chain-module layer for every single auto-swap. [3](#0-2) 

Because inbound ballot finalization, and outbound observation-vote finalization, can each take an unpredictable number of blocks (whatever it takes for 2/3 of UVs to vote), the block at which the swap actually executes is decoupled from the block at which the user deposited funds or the outbound was created. An unprivileged attacker who can influence the on-chain WPC/PRC20 pool price (e.g., an MEV searcher trading against the same pool, which is a normal, non-privileged action) can manipulate the pool spot price immediately before the swap executes so that the live `QuoterV2` quote reflects a manipulated price, and the fixed 5%-off floor still permits the swap to clear — extracting up to the full 5% tolerance from every gas top-up or refund swap, with no way for the affected user to have specified a tighter bound or an expiry to prevent the swap from landing after the price moved against them.

### Impact Explanation
Every `GAS`/`GAS_AND_PAYLOAD` inbound deposit and every gas-refund-with-swap on outbound finalization routes through this fixed, protocol-chosen slippage tolerance with no deadline. An attacker who sandwiches the WPC/PRC20 pool around the swap's actual execution block can systematically extract value (up to 5% of each affected inbound/refund amount) from ordinary users' deposits without needing any privileged role — this is unauthorized draining of user-controlled funds during universal execution, in scope per "stealing, draining ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting, refund accounting."

### Likelihood Explanation
Likelihood depends on liquidity depth of the on-chain WPC/PRC20 pool(s) and the gap between deposit/outbound-creation time and ballot finalization time; the wider that gap and the shallower the pool, the more attractive and reliable the extraction becomes. It requires no validator or admin privilege — only ordinary trading against a public AMM pool the protocol itself created — so it is reachable by any unprivileged party running an MEV bot.

### Recommendation
Do not hardcode the slippage tolerance in the keeper. Where feasible, expose the acceptable minimum-output as a parameter derived from data the depositing/paying party actually agreed to (or bound it more tightly / make it governance-tunable per token liquidity), and pass a real, bounded `deadline` (current block time + a short window) into `depositPRC20WithAutoSwap`/`refundUnusedGas` instead of `0`, so a stale-priced swap cannot execute arbitrarily late after ballot finalization. Consider re-quoting immediately before the swap call in the same tx (already done) but tightening/verifying the tolerance against realistic pool depth, and add monitoring/alerts on realized slippage.

### Proof of Concept
1. Attacker observes a pending `GAS_AND_PAYLOAD`/`GAS` inbound accumulating UV votes toward the 2/3 threshold (visible via `PendingInbounds`).
2. Attacker trades against the on-chain WPC/PRC20 Uniswap V3 pool just before the vote that will finalize the ballot lands, moving the pool price so `quoteExactInputSingle` returns a lower `amountOut`.
3. When the final vote finalizes the inbound, `k.GetSwapQuote` returns the manipulated quote; `minPCOut = quote*95/100` is computed from this already-depressed price. [1](#0-0) 
4. `CallPRC20DepositAutoSwap` executes with `deadline=0` (no staleness check), so the swap clears at the manipulated price, and the attacker reverses their trade afterward, capturing the difference as profit taken from the user's deposited PRC20. [3](#0-2)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-145)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))
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

**File:** x/uexecutor/keeper/outbound.go (L213-245)
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

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
```
