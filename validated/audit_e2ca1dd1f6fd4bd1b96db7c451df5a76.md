### Title
Fixed 5% slippage tolerance on module-driven gas-abstraction and gas-refund swaps enables sandwich extraction of protocol/user funds - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
The Curve report concerns a value (admin fee) that is fixed/snapshotted at one point in time and then used for later accounting without re-checking the current, potentially-moved state of the underlying pooled asset, letting a party extract more value than they are entitled to. Push Chain's own AMM-swap legs inside `x/uexecutor` have the analogous defect: a price quote is fetched, a slippage bound is derived from it with a hardcoded, generous 5% tolerance, and the swap is then executed — all driven by the module itself, with no protection against the underlying UniversalCore swap pool being moved by an ordinary user transaction included in the same block.

### Finding Description
Two module-originated swap paths compute `minPCOut` this way:

1. Gas-abstraction inbound execution — `ExecuteInboundGas`: [1](#0-0) 

2. Outbound gas refund — `applyGasRefund`: [2](#0-1) 

In both cases the flow is: fetch a live quote from the UniversalCore/quoter contract (`GetSwapQuote` / `getSwapQuoteForRefund`), derive `minPCOut = quote * 95 / 100`, and then immediately call `CallPRC20DepositAutoSwap` or `CallUniversalCoreRefundUnusedGas`, both of which are `DerivedEVMCall`s that execute as ordinary EVM transactions inside normal block processing (see `DERIVED_TRANSACTIONS.md`, describing these as real, receipted transactions executed at ordinary block-processing time, not in a privileged, MEV-protected slot).

Because `MsgVoteInbound`/`MsgVoteOutbound` (which trigger these swaps once the UV quorum threshold is met) are ordinary, publicly-broadcast Cosmos transactions, an unprivileged external actor observing the mempool can:
- submit a transaction immediately before the vote-triggering transaction that moves the UniversalCore swap pool's price against the pending swap (e.g., buying up the PC side of the pool), and
- submit a transaction immediately after that restores the price and captures the difference,

extracting up to the full 5% slippage tolerance from the module's swap execution — funds that come out of either the depositor's expected mint amount (gas-abstraction path) or the relayer's/sender's gas refund (refund path). Since the slippage bound is a fixed percentage of the pre-manipulation quote rather than something bound to a manipulation-resistant price source (e.g. TWAP), it does not "account for" the pool being moved between quote and execution, exactly mirroring the Curve report's core defect: a stored/derived value used for later fund movement doesn't track the real-time state of the underlying asset it represents.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... or unauthorized state transitions in universal execution flows" and "unauthorized ... release ... of user or protocol-controlled funds" in the allowed impact gate. An unprivileged attacker can systematically siphon value from every gas-abstraction deposit and every outbound gas refund that goes through the auto-swap paths, at the expense of the depositing user (who receives less PC than the fair quote) or the protocol/relayer (during refunds). This is reachable purely through ordinary transaction submission with no compromised validator, UV, or admin key required.

### Likelihood Explanation
Likelihood is limited by the requirement that the attacker have liquidity to move the specific UniversalCore pool meaningfully and by mempool-ordering assumptions (no strict FIFO/MEV protection assumed), but the mechanism itself needs no special access — any user can submit ordinary EVM transactions against the same pool the module swaps through, and the 5% band is generous relative to typical DEX slippage protections (usually far tighter or based on manipulation-resistant oracles).

### Recommendation
Replace the fixed 5% `minPCOut` derivation with a slippage bound computed from a manipulation-resistant reference price (e.g., a TWAP oracle or an on-chain price feed independent of the immediately-precedent trade), or significantly tighten the tolerance and add same-block sandwich detection/rejection. At minimum, document (as the Curve recommendation suggests) that these hardcoded-slippage swap legs do not protect against price movement within the same block, and treat that as an explicit, accepted risk rather than an implicit assumption.

### Proof of Concept
1. Attacker monitors the mempool for a `MsgVoteOutbound` (or the final vote of `MsgVoteInbound` for a `GAS`/`GAS_AND_PAYLOAD` inbound) that will trigger `applyGasRefund` / `ExecuteInboundGas`.
2. Attacker submits (with higher gas/priority) a swap against the same UniversalCore pool used by `GetSwapQuote`/`getSwapQuoteForRefund`, moving the price such that the eventual `quote` computed by the module is inflated relative to true value.
3. The vote transaction executes, the module computes `minPCOut = quote * 95/100` off the manipulated quote, and calls `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, executing at the worse, still-manipulated price (within the 5% band).
4. Attacker submits a follow-up transaction restoring the pool price, netting the difference — up to ~5% of the swapped notional — extracted from the depositor's minted PC or the refunded gas amount. [3](#0-2) [4](#0-3)

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
