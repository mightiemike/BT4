## Analysis

The reported Superform bug is a **whitelisted-action / value-manipulation** class: a privileged flow authorizes an on-chain swap where the slippage bound is derived from data that an unprivileged actor can manipulate immediately before execution, letting the actor sandwich the swap and extract value. The Push Chain analog lives in `x/uexecutor`'s **auto-swap path**, which the protocol itself (not the user) triggers whenever a GAS or GAS_AND_PAYLOAD inbound is finalized, and in the outbound gas-refund swap path.

### The pattern

Both `gasAndPayloadDepositAutoSwap` and `applyGasRefund` compute `minPCOut` from a **spot-price quote** taken from Uniswap V3's `QuoterV2.quoteExactInputSingle`, then immediately execute the real swap with a flat 5% slippage tolerance on top of that same spot quote: [1](#0-0) 

```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
``` [2](#0-1) 

The same pattern repeats in `gasAndPayloadDepositAutoSwap` and in the outbound gas refund logic: [3](#0-2) [4](#0-3) 

`GetSwapQuote` is a simulated (`commit=false`) call into the pool's `QuoterV2` at whatever state the pool is in the instant the module executes the deposit — it is a spot quote, not a TWAP or any manipulation-resistant oracle: [5](#0-4) 

An upgrade note confirms this was explicitly introduced to replace an even weaker zero-slippage call, but the "fix" still ties the bound to the manipulable spot price rather than a robust oracle: [6](#0-5) 

### Why this maps to the report's bug class

In the Superform report, the vulnerable pattern is: a privileged actor triggers a swap whose slippage-protecting parameter is derived from data that isn't fixed ahead of time and can be adversarially set at execution time, enabling value extraction via sandwiching. Here, `x/uexecutor` (a module account, acting as the "privileged" executor on behalf of the depositing user) triggers a real, on-chain, atomic quote-then-swap against a public Uniswap V3 pool. Because the pool price used for the quote is read live at execution time, any unprivileged actor who can predict when the module's auto-swap will land in a block (e.g., by watching for the `MsgVoteInbound`/`MsgVoteOutbound` finalizing vote, which is itself broadcastable/observable, gasless, and open to any UV or via mempool observation) can:

1. Front-run with a large swap on the WPC/PRC20 pool to move the price against the module's swap direction.
2. Let the module's `GetSwapQuote` + `CallPRC20DepositAutoSwap` execute atomically against the now-manipulated pool, satisfying its own 5%-off-spot `minPCOut` (which was computed from the manipulated price, not a fair price).
3. Back-run to restore the pool and capture the difference — a sandwich attack whose victim is the crosschain depositor (their bridged funds are converted to fewer PRC20/PC than a fair swap would have yielded) or the gas-refund recipient.

This is reachable by an ordinary unprivileged user (anyone able to trade on the pool and observe pending votes), requires no admin/validator/TSS compromise, and directly corrupts the amount of native/PRC20 asset a user or refund recipient actually receives — matching the in-scope impact "corruption of PRC20 or native asset accounting... or refund accounting" and "stealing... of user or protocol-controlled funds."

### Title
Spot-price-derived `minPCOut` in protocol-triggered auto-swaps enables sandwich attacks on user deposits and gas refunds - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The GAS / GAS_AND_PAYLOAD inbound-deposit auto-swap and the outbound gas-refund swap both compute their slippage floor (`minPCOut`) from an instantaneous `QuoterV2.quoteExactInputSingle` spot quote taken immediately before executing the real swap, with only a flat 5% discount applied. Since the quote and the swap both read/act on live pool state within the same block, an unprivileged actor can sandwich the module's atomic quote+swap by moving the pool price beforehand and restoring it afterward, extracting value from the user's bridged funds or refund amount.

### Finding Description
`GetSwapQuote` performs a non-committing call to the configured Uniswap V3 `QuoterV2` to obtain `amountOut` for the current pool reserves [5](#0-4) . Immediately afterward, `minPCOut` is derived as `quote * 95 / 100` and passed straight into `CallPRC20DepositAutoSwap`, which performs the actual swap via `depositPRC20WithAutoSwap` on `UniversalCore` [7](#0-6) . The identical quote→execute pattern is used for GAS_AND_PAYLOAD inbounds [8](#0-7)  and for outbound gas refunds that opt to swap back to native PC [9](#0-8) .

None of these paths use a manipulation-resistant price source (e.g., a TWAP oracle or an external, pre-committed price bound); the "protection" is a fixed 5% band on top of whatever the pool's instantaneous state happens to be at the moment the module's transaction executes. Because the module transaction (the vote that finalizes the inbound/outbound ballot) is a normal, observable, gasless transaction, and Push Chain's swap pools are ordinary public Uniswap V3 pools, any unprivileged trader can manipulate the pool immediately before the module's transaction lands in the block and reverse it immediately after, capturing the spread while the deposited/refunded value is quoted and executed at the manipulated price.

### Impact Explanation
Every GAS and GAS_AND_PAYLOAD crosschain deposit that goes through the auto-swap path, and every outbound gas refund that uses the swap-back path, is exposed to value extraction proportional to how much the attacker can move the pool within the 5% band (and the band itself provides no protection since the quote is computed from the already-manipulated price). This directly reduces the PC/PRC20 amount credited to the depositing user's UEA or the refund recipient — a concrete, repeatable loss of protocol/user-controlled value on essentially every auto-swapped inbound, matching the "corruption of ... gas fee accounting, refund accounting ... token mapping" and "stealing ... of user or protocol-controlled funds" impact categories.

### Likelihood Explanation
High. No privileged access is required — only capital to trade on the relevant pool and the ability to observe or predict when the finalizing `MsgVoteInbound`/`MsgVoteOutbound` transaction will be included (these are ordinary, gasless, publicly broadcastable transactions, and pending votes/ballot state can be observed via RPC before finalization). The swap size (deposit `amount`) is also attacker-influenced up front since the attacker/user controls the size of their own crosschain deposit, making the attack self-triggerable.

### Recommendation
Do not derive the slippage bound from a spot quote taken in the same execution as the swap. Use a manipulation-resistant reference (e.g., a TWAP over multiple blocks, or a price bound sourced from `x/uregistry`'s chain-meta/gas-price oracle validated by Universal Validators) to compute `minPCOut`, or require external validator consensus on the acceptable price band before executing `depositPRC20WithAutoSwap` / `refundUnusedGas`. At minimum, tighten or make configurable the slippage tolerance and add a maximum price-impact circuit breaker so large single-block price deviations abort the swap and fall back to the no-swap deposit path (which already exists for other error cases).

### Proof of Concept
1. Attacker monitors the Push Chain mempool/RPC for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will finalize a GAS/GAS_AND_PAYLOAD inbound ballot (any UV's vote crossing the 2/3 threshold), or for `handleSuccessfulOutbound`/`handleFailedOutbound` processing that will trigger `applyGasRefund` with `withSwap=true`.
2. Attacker submits, with higher gas priority (or via validator/proposer-adjacent ordering), a large swap on the PRC20/WPC Uniswap V3 pool identified by `GetUniversalCoreQuoterAddress`/`GetDefaultFeeTierForToken` moving the price unfavorably for the upcoming deposit direction.
3. The finalizing transaction executes: `GetSwapQuote` [10](#0-9)  reads the now-manipulated pool state, `minPCOut` is set to 95% of that manipulated quote, and `CallPRC20DepositAutoSwap` executes the real swap at the bad price — satisfying its own slippage check trivially since the check itself was computed post-manipulation.
4. Attacker submits a reverse swap immediately after to restore the pool price, pocketing the difference extracted from the module's swap. The depositing user's UEA (or the gas-refund recipient) receives materially less PC/PRC20 than a fair-price swap would have produced.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
