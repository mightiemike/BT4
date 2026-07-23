Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the router becomes `msg.sender` inside the pool and is forwarded as `sender` to the extension. A pool admin who allowlists the router to enable allowlisted users to trade through the standard periphery inadvertently opens the gate to every user, because the extension sees only the router address and approves unconditionally.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [3](#0-2) 

The actual end user's address is stored only in transient callback context via `_setNextCallbackContext` for payment settlement and is never forwarded to the pool or any extension: [4](#0-3) 

The extension therefore has no way to distinguish which end user initiated the router call. When the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every call arriving through the router, regardless of who the actual end user is.

## Impact Explanation
`SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may trade against a pool. Bypassing it lets unauthorized users execute swaps that the pool admin explicitly intended to block. For pools with institutional-only liquidity, KYC-gated market making, or favorable oracle pricing reserved for specific counterparties, this allows arbitrary users to drain LP value through unrestricted swaps. This satisfies "broken core pool functionality causing loss of funds" and "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path."

## Likelihood Explanation
The trigger requires only two ordinary, expected operational actions: (1) a pool admin deploys a pool with `SwapAllowlistExtension` on `BEFORE_SWAP_ORDER`, and (2) the pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to let allowlisted users trade through the standard periphery. After step 2, any user can call `MetricOmmSimpleRouter.exactInputSingle()` and bypass the allowlist. No special privilege, flash loan, or oracle manipulation is needed. The admin action is not malicious; it is the expected operational step, and the bug is that it has an undocumented, fund-impacting side effect.

## Recommendation
The extension must verify the actual end user, not the intermediary. Two sound approaches:

1. **Pass the real swapper in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool admin allowlists individual users, not the router.
2. **Dedicated router allowlist**: Maintain a separate `allowedRouter` set. When `sender` is an allowlisted router, decode the real swapper from `extensionData` and check that address against `allowedSwapper`.

The router address must never be the identity that the allowlist gates on.

## Proof of Concept
```
Setup
─────
1. Deploy pool with SwapAllowlistExtension on BEFORE_SWAP_ORDER.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // to let alice use the router

Attack
──────
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, ..., recipient: bob})
   Inside pool.swap() [MetricOmmPool.sol L230-240]:
       msg.sender = router
       _beforeSwap(router, ...)
   Inside SwapAllowlistExtension.beforeSwap(sender=router, ...) [SwapAllowlistExtension.sol L37]:
       allowedSwapper[pool][router] == true  →  check passes
5. Bob's swap executes and settles against LP funds.

Result: Bob, who was never allowlisted, successfully swaps against the pool,
        bypassing the SwapAllowlistExtension entirely.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
