## Finding Confirmed [1](#0-0) 

### Title
Uniswap-style spot-price quote used for gas refund swaps enables same-block price manipulation to drain WPC/PRC20 pool reserves - (File: x/uexecutor/keeper/outbound.go)

### Summary
`applyGasRefund` computes the refund swap's minimum acceptable output (`minPCOut`) by taking a **spot** quote from a Uniswap V3–style `QuoterV2.quoteExactInputSingle` call and applying a fixed 5% slippage buffer, then immediately executes the real swap against the same pool in the same finalization flow. Because the quote and the actual swap both read the pool's live tick/liquidity state with no TWAP or external price reference, an attacker who can shift the pool price shortly before the outbound-finalizing vote is processed controls both the reference quote and the executed swap price.

### Finding Description
`getSwapQuoteForRefund` fetches the current spot quote directly from the on-chain Uniswap V3 Quoter: [2](#0-1)  and `GetSwapQuote` calls `quoteExactInputSingle` with no TWAP averaging, only the instantaneous pool state: [3](#0-2) .

`applyGasRefund` then derives `minPCOut` as 95% of that same spot quote and immediately performs the real swap via `CallUniversalCoreRefundUnusedGas(..., withSwap=true, fee, minPCOut)`: [4](#0-3) . Since `refundAmount` (the PRC20 amount going into the swap) is fixed by `gasFee - gasFeeUsed` and is independent of pool price, only the WPC amount coming *out* of the swap is price-sensitive. Whoever controls the pool price at the moment the refund executes controls how much WPC the module pays out for a fixed PRC20 input.

Because the quote-fetch and the swap execution both occur inside the same `MsgVoteOutbound`-triggered state transition with no cross-block delay and no TWAP protection, an attacker who is the refund recipient (i.e., the original sender of the bridging transaction whose excess gas is later refunded) can, in the same block (or immediately preceding block) that the honest UVs' outbound-finalizing vote lands, submit an ordinary swap against the WPC/gasToken pool to push the price so that the quote — and the resulting real swap — returns far more WPC than the fair value of the refunded gas token. This is a standard oracle/spot-price manipulation pattern applied to a protocol-internal AMM pool that is used to fund refunds from module (protocol) reserves.

### Impact Explanation
An attacker with sufficient capital to move the WPC/gasToken pool price (a pool sized for gas-refund conversions is likely to have modest liquidity relative to a well-funded attacker) can extract more WPC per unit of refunded gas token than the fair market price, effectively draining WPC/protocol liquidity through their own legitimate refund. This is an unauthorized-refund / refund-accounting-corruption impact against protocol-controlled funds, reachable purely through ordinary user transactions (their own cross-chain send plus ordinary swaps on the pool) with no privileged actor required.

### Likelihood Explanation
Exploitability depends on: (1) the attacker being the recipient of their own pending gas refund, (2) the WPC/gasToken pool having exploitable liquidity depth relative to the attacker's capital, and (3) the attacker being able to time an ordinary swap to land in the same or an adjacent block as the outbound-finalizing vote (feasible via mempool observation of pending `MsgVoteOutbound` transactions once enough UVs have voted). This is a realistic MEV/sandwich scenario rather than a theoretical one, and does not require any malicious validator, TSS signer, or privileged actor — only an unprivileged user submitting ordinary swap transactions.

### Recommendation
Do not rely on a single instantaneous `QuoterV2` spot quote for both the reference price and the min-out bound of the same transaction. Use a manipulation-resistant reference price (e.g., a TWAP over a sufficient window, or an external oracle) to bound `minPCOut`, and/or cap the maximum WPC payout per refund independent of pool state, and/or restrict refund-swap pool depth/exposure so a single-block manipulation cannot materially affect payout.

### Proof of Concept
1. Attacker performs a cross-chain transaction that results in a pending outbound with an excess `GasFee` (i.e., `gasFee > gasFeeUsed`), with themselves (or their `RevertInstructions.FundRecipient`) as the refund recipient.
2. Attacker monitors the mempool/UV voting progress for the corresponding `MsgVoteOutbound` that will finalize this outbound.
3. Shortly before finalization is expected to land, attacker submits an ordinary large swap against the WPC/gasToken pool to skew the pool's spot price so gasToken appears artificially valuable in WPC terms.
4. When `applyGasRefund` → `getSwapQuoteForRefund` fetches the quote, it reflects the skewed price; `minPCOut` is derived from this skewed quote, and `CallUniversalCoreRefundUnusedGas` executes the swap against the still-skewed (or attacker-controlled) pool state, paying out inflated WPC to the attacker's `recipientAddr` for the same fixed `refundAmount` of gas token. [5](#0-4) 
5. Attacker subsequently reverses their initial swap to restore the pool and realize the extracted profit, net of their own trading costs.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L198-234)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L259-269)
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
