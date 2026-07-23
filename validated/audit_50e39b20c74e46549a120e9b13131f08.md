Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of originating trader, allowing any EOA to bypass per-trader swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, that address is the router contract, not the originating EOA. A pool admin who allowlists the router inadvertently grants swap access to every EOA using the router, completely bypassing per-trader curation.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks: [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly: [3](#0-2) 

So `sender = address(router)`. The check becomes `allowedSwapper[pool][router]`, completely ignoring the originating EOA. No existing guard recovers the originating user — the router stores the payer in transient storage for callback payment purposes only, and that value is never forwarded to the extension.

## Impact Explanation
The swap allowlist is the primary mechanism for pool curation. Any pool that allowlists the router (a natural operational choice) exposes itself to unrestricted trading by all router users. LPs who deposited into a curated pool expecting only vetted counterparties face direct fund loss through adverse selection or violation of compliance constraints the pool was designed to enforce. This constitutes broken core pool functionality causing loss of funds.

## Likelihood Explanation
The router is a standard, publicly deployed periphery contract. Pool admins operating curated pools will naturally allowlist it for UX reasons. The bypass requires no special privileges — any EOA with tokens can call `router.exactInputSingle()`. The condition is reachable on every curated pool that allowlists the router.

## Recommendation
Pass the originating user through the extension rather than the immediate `msg.sender`. One approach: add an `originator` field to the extension data that the router populates with `msg.sender` before calling the pool. The extension can then decode and verify `allowedSwapper[pool][originator]`. Alternatively, document clearly that allowlisting the router grants access to all router users and provide a separate per-user gating mechanism that the router populates via `extensionData`.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension in BEFORE_SWAP_ORDER
// 2. Pool admin allowlists only the router:
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// 3. Non-allowlisted EOA calls the router:
vm.prank(nonAllowlistedEOA);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: nonAllowlistedEOA,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));

// 4. Swap succeeds — allowedSwapper[pool][router] = true, originating EOA never checked
// 5. Assert LP token1 balance decreased (pool traded with non-allowlisted user)
assertLt(token1.balanceOf(address(pool)), token1BalanceBefore);
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
```
