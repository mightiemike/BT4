Based on this investigation, I have enough evidence to write up the finding. The pattern confirms: every autoswap path (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, `applyGasRefund`) fetches a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote and derives `minPCOut` as a flat 95% of that quote, then immediately executes the real swap in the same block via `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`. There is no independent floor, no TWAP, and the quote-then-swap sequence is not atomic against sandwiching by ordinary chain users who can trade against the same Uniswap V3 pool on Push Chain.

### Title
Spot-price slippage bound in PRC20 auto-swap deposits enables sandwich extraction from user gas/CEA deposits - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report's core defect — a deposit/conversion flow that trusts a manipulable slippage bound instead of verifying the value actually received — has a direct analog in Push Chain's `GAS`/`GAS_AND_PAYLOAD` inbound auto-swap path and in outbound gas-refund swaps. `GetSwapQuote` reads a live spot quote from the on-chain Uniswap V3 `QuoterV2` [1](#0-0) , and every caller computes `minPCOut = quote * 95 / 100` before immediately executing the real swap in the same keeper call [2](#0-1) [3](#0-2) [4](#0-3) . Like the ERC5095 report, the fixed percentage slippage tolerance is applied to a quote that is itself attacker-influenceable, so it fails to guarantee the recipient actually receives fair value.

### Finding Description
`GetSwapQuote` calls `quoteExactInputSingle` on the configured Uniswap V3 quoter at the moment of execution (spot price of the live pool), not a time-weighted average [1](#0-0) . Every call site — `ExecuteInboundGas` (gas-abstraction inbound swap), `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound swap), and `applyGasRefund`'s refund-with-swap step (outbound gas refund) — derives `minPCOut` as a flat `quote * 95 / 100` and passes it straight into the real swap executed moments later in the same transaction/block via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` [5](#0-4) [6](#0-5) [7](#0-6) .

Because the Uniswap V3 pool referenced by the quoter lives on Push Chain itself and is reachable by any unprivileged EVM account, an attacker can submit an ordinary swap transaction that moves the PRC20/WPC pool price immediately before the validator's `MsgVoteInbound` transaction that completes quorum and triggers this auto-swap deposit (or before the block containing the outbound-refund logic). Since the quote and the "protected" swap both execute back-to-back within the same block/tx with no intervening independent price check, the 5% band is computed off the manipulated price rather than off the price the module actually intended to guarantee, defeating the purpose of the slippage check entirely — exactly the flaw the external report calls out (a floor derived from an already-corrupted reference amount, with no assertion that the recipient receives at least the input's fair value).

This affects module-controlled EVM execution (`DerivedEVMCall`) that mints/converts PRC20 balances for user-owned UEAs and refunds, i.e., "corruption of PRC20 or native asset accounting" and "unauthorized module-originated `DerivedEVMCall`" paths that the allowed-impact gate calls out explicitly.

### Impact Explanation
A successful sandwich lets an attacker extract value from the WPC/PRC20 pool at the expense of the recipient's deposited/refunded amount: the recipient's UEA receives less WPC (used to pay gas / for their gasless operations) than fair market conversion would provide, while the attacker profits from the price round-trip. This is a loss of user funds during ordinary deposit/gas-abstraction and refund flows, reachable purely by an unprivileged external actor trading on the pool — no validator, relayer, or admin collusion required.

### Likelihood Explanation
Likelihood is moderate: the attacker needs to (a) predict or observe when a `MsgVoteInbound` tx that reaches quorum (or an outbound settlement) will land in a block, and (b) get their manipulation swap ordered immediately before it. Because inbound votes are broadcast transactions visible in the mempool and Push Chain is a single shared-sequencer chain (not encrypted mempool), block-level transaction ordering can plausibly be influenced by gas/fee bidding, making this a realistic MEV/sandwich scenario rather than a purely theoretical one. The fixed 5%, non-TWAP tolerance is applied identically across all three call sites, so the exposure is systemic rather than a one-off.

### Recommendation
Do not derive the slippage floor solely from a spot quote fetched in the same execution as the swap. Options: (1) use a TWAP-based quote (time-weighted average over N blocks) from the Uniswap V3 pool instead of `quoteExactInputSingle`'s spot output; (2) additionally enforce a protocol-level absolute floor unrelated to the same-block quote (e.g., a recent on-chain price oracle or a governance-configured minimum conversion rate) so a manipulated spot quote cannot silently pass; (3) reduce the atomicity window by disallowing user-submitted swaps against the WPC/PRC20 pools within the same block as module-driven auto-swap executions, or route module auto-swaps through a mechanism resistant to same-block sandwiching.

### Proof of Concept
1. Attacker observes a `MsgVoteInbound` transaction in the mempool that will bring an inbound (`TxType_GAS` or `TxType_GAS_AND_PAYLOAD`) to quorum, triggering `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`.
2. Attacker submits (with a higher gas price / earlier in block ordering) a large swap against the same PRC20/WPC Uniswap V3 pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, moving the pool's spot price unfavorably for the upcoming module swap.
3. The validator's vote tx executes next in the block; `GetSwapQuote` in `ExecuteInboundGas` reads the now-manipulated spot price and computes `minPCOut = quote*95/100` off that skewed value [2](#0-1) .
4. `CallPRC20DepositAutoSwap` executes the swap for the victim's UEA against the still-manipulated pool, satisfying the (also-skewed) `minPCOut`, but the UEA receives materially less WPC than a fair-price swap would have produced.
5. Attacker reverses their position later in the same or a following block, capturing the price-impact profit at the victim's expense.

### Citations

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
