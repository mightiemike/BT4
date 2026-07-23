Audit Report

## Title
PriceVelocityGuardExtension Velocity Check Silently Bypassed on First Swap Due to Zero-Initialized `lastMidPriceX64` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary
`PriceVelocityGuardExtension.beforeSwap` gates all velocity math behind `if (prevMid != 0)`. Because `lastMidPriceX64` is a plain mapping field that defaults to zero, and the extension provides no `initialize` override to seed it at pool-creation time, the velocity guard is silently skipped on the first swap of every pool using this extension. Any unprivileged user who executes the first swap after a significant oracle price move causes LPs to absorb the full adverse selection with no protection.

## Finding Description
In `beforeSwap` (lines 54–76), the prior mid-price is read, the new state is written unconditionally, and then the velocity math is gated:

```solidity
uint128 prevMid = s.lastMidPriceX64;   // reads 0 on first swap
s.lastMidPriceX64 = midPrice;           // state written unconditionally
s.lastUpdateBlock = uint64(block.number);

if (prevMid != 0) {                     // always false on first swap → guard skipped
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        // velocity math and revert
    }
}
```

`priceVelocityState` is `mapping(address pool => PriceVelocityState)` (line 20); Solidity zero-initializes all mapping values, so `lastMidPriceX64` is `0` for every newly deployed pool. The extension does not override `initialize` — the base `BaseMetricExtension.initialize` (lines 41–43) is a no-op that simply returns the selector. The only seeding path is the admin-only `setLastMidPrice` (lines 29–34), which is optional and not called automatically by any factory or deployment flow. There is no on-chain precondition enforcing that `setLastMidPrice` must be called before the pool accepts swaps.

Exploit path:
1. Pool deployed with `PriceVelocityGuardExtension`; admin sets `maxChangePerBlockE18 = 1e15` (≈ 0.1% per block). `setLastMidPrice` is not called.
2. Oracle price moves 30% between deployment and block N (legitimate market move).
3. Any unprivileged user calls `pool.swap(...)` at block N. Inside `beforeSwap`: `prevMid = 0` → `if (prevMid != 0)` is false → velocity math never executes → no revert.
4. Swap settles at the 30%-moved price; LPs absorb the full adverse selection.
5. A second swap in the same block with another 1% move would correctly revert with `PriceVelocityExceeded`.

## Impact Explanation
The velocity guard's sole purpose is to protect LPs from rapid oracle-price movements that cause LVR (loss versus rebalancing). Bypassing it on the first swap allows a trader to execute at an arbitrarily moved price, causing LPs to sell tokens at a worse-than-intended rate. The loss is bounded by the magnitude of the oracle price move and the pool's liquidity depth, but is a direct, concrete loss of LP principal. This matches the allowed impact: "bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap" and "direct loss of user principal."

## Likelihood Explanation
The condition is met for every pool deployed with this extension unless the admin manually calls `setLastMidPrice` before the first swap. There is no on-chain enforcement of that precondition. Any unprivileged user can trigger the first swap; no special role, flashloan, or setup is required beyond the pool being live. The window between pool deployment and first swap is the only requirement, and oracle prices routinely move during that window for volatile assets.

## Recommendation
**Option A (fail closed):** At the start of `beforeSwap`, if `maxChangePerBlockE18 != 0` and `prevMid == 0`, revert with a dedicated error (e.g., `PriceVelocityNotInitialized`). This forces the admin to call `setLastMidPrice` before the pool accepts swaps.

**Option B (initialize at deployment):** Override `initialize` in `PriceVelocityGuardExtension` to accept and store an initial `lastMidPriceX64` and `lastUpdateBlock`, mirroring the pattern used by `OracleValueStopLossExtension.initialize` (lines 46–68), which is called once by the factory at pool creation and enforces that configuration is present before the pool is live.

## Proof of Concept
```solidity
// Foundry test sketch
function test_firstSwapBypassesVelocityGuard() public {
    // 1. Deploy pool with PriceVelocityGuardExtension
    // 2. Admin sets maxChangePerBlockE18 = 1e15 (0.1%/block)
    //    but does NOT call setLastMidPrice
    // 3. Oracle price moves 30% (update bid/ask in oracle)
    // 4. Unprivileged user calls pool.swap(...)
    //    → beforeSwap: prevMid = 0 → if(prevMid != 0) false → no revert
    //    → swap executes at 30%-moved price
    // 5. Assert LP balances decreased by ~30% adverse selection
    // 6. Second swap with 1% move in same block → PriceVelocityExceeded revert
}
```