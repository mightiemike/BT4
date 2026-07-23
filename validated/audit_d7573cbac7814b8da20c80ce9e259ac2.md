Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling complete allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the pool's `msg.sender` is the router contract, not the end user. Any non-allowlisted user can therefore bypass a curated pool's swap gate by routing through the public router, completely defeating the access control.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [3](#0-2) 

The pool's `msg.sender` is therefore the router address. The extension receives `sender = router` and evaluates `allowedSwapper[pool][router]` — never touching the actual end user's allowlist entry. The same misbinding applies to `exactInput` multi-hop paths: [4](#0-3) 

There is no mechanism in the router to forward the original caller's identity to the extension, and no guard in the extension to recover it. The `extensionData` field is passed through unchanged from the user's call parameters and is not used by `SwapAllowlistExtension`.

## Impact Explanation
Two fund-impacting outcomes arise from this misbinding:

1. **Allowlist bypass (High):** If the pool admin allowlists the router address (the only way to let any user reach the pool through the router), every non-allowlisted user can swap freely by routing through `MetricOmmSimpleRouter`. The curated pool's access control is completely defeated, allowing unauthorized traders to drain liquidity at oracle prices — a direct loss of LP principal.

2. **Broken core functionality (Medium):** If the admin does not allowlist the router, allowlisted users cannot use the router at all — their own allowlist entry is never checked. The primary user-facing swap path is silently unusable for every legitimate user.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` to restrict trading will encounter this misbinding the first time any user routes through the router. The trigger requires no special timing, no oracle manipulation, and no admin cooperation beyond normal pool setup. The bypass is repeatable by any public caller.

## Recommendation
The extension must gate the economic actor — the end user — not the intermediate contract. Two complementary fixes:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling the pool; the extension decodes and checks that address. This requires a coordinated convention between the router and the extension.

2. **Check `tx.origin` as a fallback:** For allowlist purposes, `tx.origin` always identifies the EOA initiating the transaction. While generally discouraged, it is semantically correct for allowlist gating and cannot be spoofed by an intermediate router.

The cleanest long-term fix is option 1, with the router always prepending the original caller's address to `extensionData` so any extension can recover the true initiator.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured in the beforeSwap order.
2. Pool admin calls setAllowedToSwap(pool, router, true)
   — necessary so that any router-mediated swap can reach the pool at all.
3. Pool admin calls setAllowedToSwap(pool, alice, true)  // alice is the only intended trader
   — bob is NOT allowlisted.
4. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
   — Router calls pool.swap(recipient=bob, ...).
   — Pool calls _beforeSwap(sender=router, ...).
   — Extension checks allowedSwapper[pool][router] == true → passes.
   — Bob's swap executes despite never being allowlisted.
5. Alice calls pool.swap() directly.
   — Extension checks allowedSwapper[pool][alice] == true → passes (correct).
6. Carol (not allowlisted) calls pool.swap() directly.
   — Extension checks allowedSwapper[pool][carol] == false → reverts (correct).

Result: Bob (step 4) bypasses the allowlist entirely through the router,
        while Carol (step 6) is correctly blocked on the direct path.
        The allowlist provides zero protection for router-mediated swaps.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
