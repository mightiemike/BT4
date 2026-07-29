### Title
Sandwichable Uniswap V3 QuoterV2 spot-price used to compute `minPCOut` for user gas-abstraction swap and gas refund, allowing single-transaction sandwich extraction of user deposits — (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
`ExecuteInboundGas` (gas-abstraction deposit-and-swap route) and `applyGasRefund` (outbound gas-fee refund route) both compute a minimum-output slippage bound by querying `QuoterV2.quoteExactInputSingle` — a spot, single-block AMM price simulation — and then apply a **fixed 5% haircut** to derive `minPCOut`, which is passed directly into `depositPRC20WithAutoSwap` / `refundUnusedGas` on the `UniversalCore` contract in the *same* Push Chain block/transaction. Because the quote and the swap execute atomically within one deterministic, attacker-observable transaction flow (inbound vote finalization → quote → swap), an attacker who controls liquidity in the underlying pool (or who can influence the pool state prior to inbound finalization, e.g. by front-running the quorum-completing vote with their own swap on the pool) can move the pool price so that the quote itself is computed against a manipulated price, and the 5% band is not sufficient protection against a purpose-built single-block manipulation, unlike a TWAP-anchored guard. This mirrors the audited BalancerStrategy `updateCache` bug class: a manipulable spot-valuation function is trusted and immediately converted into money-moving state (minted PRC20/PC to a user or a refund payout) with no external dispute window, using a fixed percentage tolerance instead of fair/TWAP-anchored pricing.

### Finding Description
- `k.GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) performs a `commit=false` (view) call to `QuoterV2.quoteExactInputSingle`, which — like Balancer's `queryExit` — simulates a trade against the *current* instantaneous reserves/sqrtPrice of the pool. [1](#0-0) 
- In `ExecuteInboundGas`, this quote is taken and immediately discounted by a flat 5%: `minPCOut := quote * 95 / 100`, then passed into `CallPRC20DepositAutoSwap` in the very same call, which executes `depositPRC20WithAutoSwap` on-chain with that bound as the slippage floor. [2](#0-1) 
- The identical pattern exists on the refund side in `applyGasRefund`, computing `minPCOut = quote * 95 / 100` from `getSwapQuoteForRefund` before calling `CallUniversalCoreRefundUnusedGas`. [3](#0-2) 
- The upgrade log explicitly documents that this quote+5% pattern *replaced* a previous 0-slippage call, i.e. the protocol is aware of price movement risk but mitigated it only with a fixed percentage rather than a manipulation-resistant oracle (TWAP, multi-block average, or bonded dispute window). [4](#0-3) 

Because inbound finalization (and therefore the swap) happens deterministically at the block where the 2/3+ validator quorum vote lands, and pool state used for the quote is read at that same execution point, an attacker who can also execute swaps against the same underlying Uniswap V3 pool used by `UniversalCore` (a pool that is otherwise open/public, since PRC20/WPC liquidity is a normal AMM pool) can:
1. Push the pool price down right before/at the block that finalizes the inbound quorum vote (single transaction ordering favors the attacker if they can predict or influence transaction inclusion order within the same block, as validators submit votes and the quoter/swap execute atomically inside `ExecuteInboundGas`).
2. Cause `GetSwapQuote` to return an artificially low `quote`, yielding a low `minPCOut`.
3. Because `minPCOut` is only a floor (not the actual expected value), the swap executes at the manipulated bad price, and the difference between fair value and executed value is captured by whoever restores the pool afterward (the same attacker), extracting value at the depositing user's expense — directly analogous to "Imbalance Down -> Liquidate/skim at a profit" in the source report.
4. Conversely, pushing the price up inflates `quote`/`minPCOut`, which can cause the swap to revert (denial of service to the legitimate depositor / refund recipient) or, if the pool has enough depth manipulation window, allow other value-extraction paths through repeated small-amount sandwiches given the fixed 5% tolerance is far larger than typical same-block price impact needed to profit from AMM fees.

### Impact Explanation
This affects two concrete money-moving flows reachable by unprivileged ordinary users' inbound deposits and by the protocol's own gas-refund flow:
- `ExecuteInboundGas` mints PC to a user's UEA via `depositPRC20WithAutoSwap`; a manipulated `minPCOut` can result in the user (or the protocol, depending on who bears the swap loss) receiving less PC than fair value, i.e. unauthorized value transfer/loss during a state transition in the universal execution flow. [5](#0-4) 
- `applyGasRefund` refunds excess relayer gas fee to users/fund recipients using the same swap-quote pattern, directly touching protocol-controlled funds accounting. [6](#0-5) 

This falls within the allowed impact gate: "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" and "stealing ... permanent loss ... of user or protocol-controlled funds," reachable via ordinary user deposit/inbound flows with honest validators — no privileged actor assumption is required, only market/pool access, which is unprivileged.

### Likelihood Explanation
Likelihood is **moderate**, contingent on facts not verifiable from the available code:
- Whether the `UniversalCore`/Uniswap V3 pool backing PRC20↔WPC swaps has sufficiently thin liquidity for an unprivileged attacker to move price meaningfully within a single block/transaction relative to typical inbound/refund amounts.
- Whether inbound vote finalization (and thus the swap) is deterministically triggerable/orderable by an attacker relative to their own sandwich transactions (Push Chain is a Cosmos-SDK chain; MEV/ordering assumptions differ from Ethereum mempools, and this repo does not expose a public mempool model in the code reviewed).
- The 5% band is a fixed, generous slippage tolerance which is more permissive than a well-designed TWAP-based bound, increasing exploitability compared to standard best practice, but I could not verify actual pool depth or fee-tier configuration (`defaultFeeTier`) from the indexed code, which materially affects real-world exploitability.

### Recommendation
- Replace or augment the single-block `QuoterV2.quoteExactInputSingle` spot quote with a manipulation-resistant reference, e.g. a time-weighted average price (TWAP) over multiple blocks, or cross-check against the `ChainMeta`/oracle gas-price median that the module already maintains via `VoteChainMeta` before accepting `minPCOut`. [7](#0-6) 
- Tighten the slippage tolerance and/or make it configurable/adaptive based on trade size relative to pool liquidity rather than a flat 5%.
- Consider deferring/splitting the quote-then-swap into two steps separated by at least one block, or requiring quorum of Universal Validators to independently attest to the swap quote (similar to `VoteChainMeta`), removing single-observer/manipulable-in-block pricing from the trust path.

### Proof of Concept
Not independently executable from the indexed code alone (no local devnet/pool-state access in this review). Conceptual PoC based on code inspection:
1. Attacker identifies the Uniswap V3 pool used for the `prc20Address`/`wpcAddress` pair via `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`. [8](#0-7) 
2. Attacker submits a large swap against that pool to move the price down, timed to land in/near the same block where an inbound quorum vote will finalize and trigger `ExecuteInboundGas`.
3. `GetSwapQuote` returns a depressed `quote`; `minPCOut = quote*95/100` is computed from the depressed value. [9](#0-8) 
4. `CallPRC20DepositAutoSwap` executes the swap at the manipulated price, converting the user's inbound asset to PC at a bad rate.
5. Attacker reverses their initial swap, restoring the pool and capturing the spread as profit, at the expense of the depositing user/protocol.

I was unable to fully verify the pool liquidity, MEV/ordering model on Push Chain consensus, and whether other unread guardrails (e.g., a minimum-liquidity check or a TWAP already used elsewhere in `UniversalCore`'s Solidity contracts, which are out of the indexed Go code) mitigate this — those Solidity contracts were not available in the indexed context and should be reviewed directly, ideally by starting a full Devin session with complete repository access, to confirm whether `UniversalCore.depositPRC20WithAutoSwap`/`refundUnusedGas` enforce any additional TWAP or oracle-based bound beyond the caller-supplied `minPCOut`.

### Citations

**File:** x/uexecutor/keeper/evm.go (L422-468)
```go
// GetUniversalCoreQuoterAddress reads the uniswapV3Quoter address stored in UniversalCore.
func (k Keeper) GetUniversalCoreQuoterAddress(ctx sdk.Context) (common.Address, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "uniswapV3Quoter")
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to call uniswapV3Quoter")
	}

	results, err := abi.Methods["uniswapV3Quoter"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to unpack uniswapV3Quoter result")
	}

	return results[0].(common.Address), nil
}

// GetUniversalCoreWPCAddress reads the WPC (wrapped PC) address stored in UniversalCore.
func (k Keeper) GetUniversalCoreWPCAddress(ctx sdk.Context) (common.Address, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "WPC")
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to call WPC")
	}

	results, err := abi.Methods["WPC"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to unpack WPC result")
	}

	return results[0].(common.Address), nil
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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L14-24)
```go
func (k Keeper) ExecuteInboundGas(ctx context.Context, inbound types.Inbound) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	ueModuleAccAddress, ueModuleAddressStr := k.GetUeModuleAddress(ctx)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)

	k.Logger().Info("execute inbound gas: gas abstraction swap",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"amount", inbound.Amount,
		"sender", inbound.Sender,
	)
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

**File:** x/uexecutor/keeper/outbound.go (L174-230)
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

**File:** x/uexecutor/keeper/chain_meta.go (L156-177)
```go
	// Compute independent upper medians (len/2) for price and chain height.
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })

	k.Logger().Debug("chain meta medians computed",
		"chain_id", observedChainId,
		"fresh_votes", len(fresh),
		"median_price", medianPrice,
		"median_chain_height", medianChainHeight,
	)

	// Update MedianIndex to reflect the price median position in the full slice
	// (best-effort; used for storage/querying only).
	entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))

	priceBig := math.NewUint(medianPrice).BigInt()
	chainHeightBig := math.NewUint(medianChainHeight).BigInt()
	if _, evmErr := k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig); evmErr != nil {
		return sdkerrors.Wrap(evmErr, "failed to call EVM setChainMeta")
	}

	entry.LastAppliedChainHeight = medianChainHeight
```
