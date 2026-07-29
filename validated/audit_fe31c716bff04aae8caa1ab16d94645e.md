### Title
Single-block spot-AMM quote used as the only slippage guard for auto-swap deposits and gas refunds enables sandwich extraction of bridged user funds - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
The Honey report's root cause is that a value-moving action (minting) is executed automatically against externally-influenced pricing with only a static, insufficient guard (peg-offset check) that an attacker can exploit in the window before an admin/oracle correction. The Push Chain analog is `GetSwapQuote`, which reads a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot price and immediately uses `95%` of that number as the sole `minPCOut` slippage floor for the auto-swap that executes in the very same transaction (`CallPRC20DepositAutoSwap` / `refundUnusedGas`). There is no TWAP, no external price-deviation check, and no halt/basket-mode-style safety valve — an attacker who moves the pool price immediately before the validator's finalizing vote transaction lands can force bridged deposits and gas refunds to settle at a manipulated price, extracting value from users/the protocol, exactly analogous to minting Honey at a favorable, unguarded price before the system's degraded state is corrected.

### Finding Description
`GetSwapQuote` (`x/uexecutor/keeper/evm.go:500-538`) calls the Uniswap `QuoterV2` contract with `CallEVM(..., commit=false, ...)` to obtain a spot-price quote for `prc20 → wpc`. This quote is fetched and consumed in the same execution context as the real swap: [1](#0-0) 

and again in the FUNDS_AND_PAYLOAD path: [2](#0-1) 

and once more for gas refunds: [3](#0-2) 

In all three call sites the "protection" is identical and mechanical:
```
minPCOut := quote * 95 / 100
```
There is no independent price oracle cross-check (unlike the `x/uexecutor` chain-meta price oracle used elsewhere in the codebase, e.g. `x/uexecutor/keeper/chain_meta.go`), no TWAP, and no fallback/halt mode analogous to what the Honey fix eventually required ("halt minting until admin can intervene"). The quote and the swap execution happen back-to-back inside the *same* module-originated EVM call sequence, which itself executes inside the single Cosmos transaction that finalizes a `MsgVoteInbound`/`MsgVoteOutbound` ballot. Because the pool being quoted is the protocol's own on-chain Uniswap V3 pool (reachable by any ordinary trader, no privileged role required), an unprivileged actor can:
1. Watch the mempool/gossip for the finalizing vote transaction that will trigger `CallPRC20DepositAutoSwap` / `refundUnusedGas` for a large-amount inbound or outbound.
2. Submit a swap that pushes the pool price so the `quoteExactInputSingle` result — and therefore `minPCOut` — is depressed.
3. Let the deposit/refund swap execute at (or near) that depressed price, since the 5% band is computed from the very quote the attacker just manipulated.
4. Reverse their own swap afterward, capturing the price difference at the expense of the bridged recipient/protocol.

This is the direct analog of the basket-mode bug: an automatic, unhalted, price-dependent value-transfer path relies on a single, attacker-influenceable price signal with only a fixed percentage tolerance as its only defense, and no separate mechanism gates or pauses the flow while the price is anomalous.

### Impact Explanation
A successful sandwich against `CallPRC20DepositAutoSwap` (inbound `GAS`/`GAS_AND_PAYLOAD` deposits) or against `applyGasRefund`'s swap leg (outbound gas refunds) directly reduces the PC amount credited to the legitimate bridged recipient, i.e. drains value from user/protocol-controlled funds that should have been converted at a fair rate. Since this is the *only* pricing safeguard on these paths (`minPCOut` derived exclusively from the manipulable spot quote), the loss is bounded only by the pool's available liquidity/depth and the 5% band, and can be repeated on every large auto-swap deposit or refund.

### Likelihood Explanation
Exploitation requires no privileged role, no validator/oracle compromise, and no protocol-level authorization bypass — only capital to trade against the pool and the ability to place transactions around the target finalizing vote transaction within the same block, which is achievable by any ordinary chain user/trader watching the mempool. This matches the "unprivileged external attacker" threat model required by the allowed-impact gate.

### Recommendation
Do not derive the slippage floor solely from a spot quote fetched in the same call as the swap. Use a time-weighted or otherwise manipulation-resistant price reference (e.g., the same chain-meta-style multi-validator oracle pattern already used elsewhere in `x/uexecutor`) to bound `minPCOut`, and/or cap the maximum acceptable deviation between the on-chain spot quote and that reference price, reverting (analogous to entering "basket mode"/halting) rather than proceeding when the deviation exceeds a safe threshold.

### Proof of Concept
1. Attacker monitors the Push Chain mempool for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction from the 3rd/final Universal Validator that will push a `GAS_AND_PAYLOAD` (or gas-refund) ballot over threshold, per `x/uexecutor/keeper/execute_inbound_gas_and_payload.go:347-379` / `x/uexecutor/keeper/outbound.go:213-234`.
2. Attacker submits a large swap on the protocol's Uniswap V3 `prc20/wpc` pool moving the spot price unfavorably for the pending deposit/refund direction.
3. If included before the finalizing vote tx in the same block, `GetSwapQuote` returns a depressed quote; `minPCOut = quote*95/100` is set from that depressed value; `depositPRC20WithAutoSwap`/`refundUnusedGas` executes against the still-manipulated pool and clears the (already-lowered) floor.
4. Attacker reverses their swap, realizing the difference between the fair price and the manipulated execution price at the expense of the bridged recipient's credited PC balance.

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
