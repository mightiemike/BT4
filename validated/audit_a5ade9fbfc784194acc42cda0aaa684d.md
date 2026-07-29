### Title
Permanent loss of excess gas-fee refunds when both refund paths fail in `applyGasRefund` - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
This is the closest native analog to the "risk pool never used" pattern: Push Chain's outbound-finalization flow computes an excess-gas amount (`gasFee - gasFeeUsed`) that is owed back to the user, attempts to release it exactly once via `UniversalCore.refundUnusedGas`, and — if that attempt fails on both the swap and no-swap code paths — simply records a `FAILED` status on `PcRefundExecution` and finalizes the outbound anyway. There is no retry mechanism, no admin recovery message, and no code path that revisits a failed refund. Like the QuailFinance risk pool, funds are earmarked for a specific purpose but can end up permanently unreachable through the intended flow.

### Finding Description
`applyGasRefund` (<cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="174,196,198" end="174,196,198" />) is invoked from both `handleSuccessfulOutbound` and `handleFailedOutbound` <cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="163,171" end="163,171" /> <cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="156,158" end="156,158" /> once UVs finalize an outbound observation. When `gasFee > gasFeeUsed`, it computes `refundAmount` and tries:

1. Swap path: `GetDefaultFeeTierForToken` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)`.
2. Fallback path if step 1 fails at any stage: `CallUniversalCoreRefundUnusedGas(..., withSwap=false, ...)` (direct PRC20 deposit).

<cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="213,245,253" end="213,245,253" />

If the fallback call also fails, the code path simply sets:
```go
refundPcTx.Status = "FAILED"
refundPcTx.ErrorMsg = err.Error()
...
outbound.PcRefundExecution = refundPcTx
outbound.RefundSwapError = swapFallbackReason
```
<cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="245,253,255" end="245,253,255" />

and the caller (`handleSuccessfulOutbound` / `handleFailedOutbound`) proceeds to call `k.UpdateOutbound(...)`, finalizing the outbound as `OBSERVED` or `REVERTED` regardless of the refund outcome <cite repo="patrichyt/push-chain-node--017" path="x/uexecutor/keeper/outbound.go" start="149,160,171" end="149,160,171" />. Once this state is persisted, there is no msg server handler, keeper method, or scheduled job in the codebase (`RetryRefund`, `MsgRetry`, etc. — none found) that revisits an outbound with `PcRefundExecution.Status == "FAILED"`. The excess gas amount that `UniversalCore` was supposed to release to the recipient is never retried and becomes permanently unreachable through any user-facing or automated flow, exactly mirroring the reported "funds taken but never used, and only recoverable through an unintended path" bug class.

This is reachable without any privileged actor: it only requires (a) honest UVs reporting `gas_fee_used < gas_fee` for an ordinary outbound (a routine, common case), and (b) the on-chain swap quote/slippage check or gas-token balance state at the time of refund causing both the swap and no-swap `refundUnusedGas` EVM calls to revert (e.g., transient DEX price movement beyond the 5% slippage tolerance for the swap leg, or state that makes the no-swap PRC20 deposit revert). Both are conditions an unprivileged party can influence by trading against the relevant pool or by timing, without needing any special role.

### Impact Explanation
The impact matches the in-scope "permanent freezing ... of user or protocol-controlled funds" category. The excess gas fee is protocol-accounted value (already deducted from the user's swap/gas allocation on the destination-chain accounting) that is meant to be returned to the user or `RevertInstructions.FundRecipient`. When both refund legs fail, this value is stranded: it is not re-attempted, not exposed via any admin rescue message analogous to `RevertStuckInbound` or `AttachRescueOutboundFromReceipt`, and the outbound record moves to a terminal status (`OBSERVED`/`REVERTED`) that closes the door on any automatic follow-up.

### Likelihood Explanation
Moderate. It requires a specific but plausible race: gas-fee-used underreporting is common (outbound execution frequently uses less gas than budgeted), and DEX-based swap/quote calls are inherently subject to slippage/liquidity conditions that an unprivileged actor can influence. The no-swap fallback failing as well (e.g., PRC20 mint/deposit revert to a bad or contract recipient) further reduces but does not eliminate likelihood. The test suite itself acknowledges this exact scenario is untested for the fully-failed case: `gas_fee_refund_test.go` only asserts `PcRefundExecution` is set and has *some* status, not that success is guaranteed or retried on failure <cite repo="patrichyt/push-chain-node--017" path="test/integration/uexecutor/gas_fee_refund_test.go" start="144,151" end="144,151" />.

### Recommendation
Either (a) do not finalize the outbound status until the refund has verifiably succeeded or is explicitly deferred, or (b) implement a genuine retry/rescue completion feature analogous to `RevertStuckInbound`/`AttachRescueOutboundFromReceipt` that lets a `PcRefundExecution.Status == "FAILED"` outbound be re-processed and the excess gas fee released, and index such outbounds (e.g., in a `PendingRefunds` collection) so they are discoverable and completable rather than silently dropped.

### Proof of Concept
1. A user submits an outbound-triggering transaction; UVs finalize the outbound with `gasFee = X`, `gasFeeUsed = Y` where `X > Y` (a routine occurrence).
2. At the moment `applyGasRefund` runs, manipulate (or simply have) the on-chain gas-token/PC swap pool such that the quote or `minPCOut` slippage check causes `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)` to revert.
3. Ensure the no-swap fallback also fails (e.g., because the recipient/PRC20 contract state reverts the direct deposit at that time).
4. `applyGasRefund` records `refundPcTx.Status = "FAILED"`; `handleSuccessfulOutbound`/`handleFailedOutbound` still calls `k.UpdateOutbound`, finalizing the outbound.
5. Inspect the codebase for any subsequent retry path (message handler, keeper method, ante/end-blocker) that re-attempts refund for outbounds with `PcRefundExecution.Status == "FAILED"` — none exists, confirming the excess gas fee value is permanently unrecoverable through intended flows.

Note: I could not fully verify on-chain Solidity behavior of `UniversalCore.refundUnusedGas` (its Solidity source is not indexed in this repository), so the exact conditions under which both legs revert are inferred from the Go-side call structure and error-handling branches rather than confirmed via the contract implementation itself.