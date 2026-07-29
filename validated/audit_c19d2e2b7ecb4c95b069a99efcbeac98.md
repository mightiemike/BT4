## Title
Same-Block Spot-Price Slippage Bound Enables Sandwich Extraction on Cross-Chain Deposit Auto-Swap and Gas Refund Swap - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The CGDA report shows that a pricing function relying on the *current* state of manipulable parameters, without any external floor/ceiling, can be driven toward a near-zero (attacker-favorable) execution price. Push Chain's Universal Executor reproduces this same class of bug in its own Uniswap V3-based conversion paths: it fetches a spot quote and derives `minPCOut` from that *same* quote inside the *same* transaction that performs the swap, so the slippage bound offers no protection against price manipulation occurring immediately before that transaction executes.

### Finding Description
Three flows compute `minPCOut` from a same-transaction `QuoterV2.quoteExactInputSingle` call and then immediately execute the swap with that bound:

- `ExecuteInboundGas` (GAS inbound autoswap): fetches `quote` via `k.GetSwapQuote(...)`, computes `minPCOut = quote * 95 / 100`, then calls `k.CallPRC20DepositAutoSwap(...)` in the same execution. [1](#0-0) 
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound autoswap): identical pattern. [2](#0-1) 
- `applyGasRefund` (outbound gas refund swap-back to PC): identical pattern via `getSwapQuoteForRefund`. [3](#0-2) 

The quote itself is a static call to `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96: 0`, i.e., a spot-price quote of the current pool state, not a TWAP: [4](#0-3) 

Because these swaps execute against the on-chain PRC20/WPC Uniswap V3 pool inside `UniversalCore`, and the pool is permissionlessly tradable by any account, an unprivileged attacker can:
1. Observe (in the mempool) that a `MsgVoteInbound`/`MsgVoteOutbound` transaction is about to reach quorum and trigger `depositPRC20WithAutoSwap` or `refundUnusedGas` (both gasless message types that are broadcast without fees, so they are cheap to watch/race against — see the gasless allowlist). [5](#0-4) 
2. Front-run with an ordinary swap that moves the pool price against the victim's PRC20→WPC leg.
3. Let the victim's autoswap/refund execute — since `minPCOut` is derived from the *already-manipulated* spot quote, the 5% buffer is computed on the manipulated price, not a fair/historical price, so it does not prevent the loss.
4. Back-run to restore the pool price and capture the extracted value.

This is the direct AMM analog of the CGDA bug: a price-dependent output value is computed from parameters (the pool's instantaneous reserves) that the same unprivileged actor can push toward an extreme in a single atomic sequence, and the only sanity check (`minPCOut`, `purchasePrice`'s implicit soundness) is derived from that same manipulable input rather than an independent bound.

### Impact Explanation
A victim user's cross-chain deposit (GAS/GAS_AND_PAYLOAD inbound autoswap) or a successful/failed outbound's unused-gas refund can be executed at a manipulated, unfavorable price, resulting in the user's UEA/recipient receiving materially less PC (or gas-token-equivalent) than the fair-market amount — a direct value-extraction (fund-draining) impact on user-controlled funds reachable via ordinary user transactions.

### Likelihood Explanation
Requires only unprivileged access to submit ordinary swap transactions against the WPC/PRC20 pool and to observe pending gasless vote transactions in the mempool — no validator, admin, or TSS privilege is needed. Feasibility depends on pool liquidity depth relative to the deposit/refund amount, which is more likely for smaller-cap or newly-listed PRC20/gas tokens.

### Recommendation
- Replace or supplement the same-block spot quote with a TWAP-based reference price (or an oracle-anchored bound) when computing `minPCOut`.
- Alternatively, bound the acceptable price deviation against a stored/last-known-good rate rather than deriving the bound purely from the instantaneous quote used for execution.
- Consider adding a maximum price-impact check independent of the quote itself.

### Proof of Concept
1. Attacker monitors mempool for a `MsgVoteInbound`/`MsgVoteOutbound` tx that will reach quorum and trigger `CallPRC20DepositAutoSwap` (path in `x/uexecutor/keeper/execute_inbound_gas.go:134-153`) or `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)` (path in `x/uexecutor/keeper/outbound.go:214-237`).
2. Attacker submits a large swap in the same WPC/PRC20 (or gasToken/WPC) Uniswap V3 pool immediately before, shifting the spot price unfavorably for the pending PRC20→WPC conversion.
3. The victim's finalizing vote transaction executes `GetSwapQuote` against the now-manipulated pool state, computes `minPCOut = quote*95/100` off that manipulated price, and executes `depositPRC20WithAutoSwap`/`refundUnusedGas` — succeeding despite receiving far less PC than fair value.
4. Attacker submits a reverse swap to restore the pool price, realizing the difference as profit extracted from the victim's cross-chain deposit/refund.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L214-230)
```go
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

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
