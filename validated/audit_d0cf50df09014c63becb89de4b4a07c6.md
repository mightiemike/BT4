Audit Report

## Title
SwapAllowlistExtension checks the router address instead of the end user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes its own `msg.sender` — the direct caller of `pool.swap()` — as `sender`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension checks whether the **router** is allowlisted, not whether the **end user** is allowlisted. Any pool admin who allowlists the router to enable legitimate users to reach the pool via the standard UX path simultaneously grants every unprivileged user the ability to bypass the individual allowlist.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap`:** [1](#0-0) 

`msg.sender` is the pool (correct). `sender` is the first positional argument forwarded by the pool. The check `allowedSwapper[msg.sender][sender]` therefore checks whether the **direct caller of `pool.swap()`** is allowlisted, not the originating user.

**What the pool passes as `sender` — `MetricOmmPool.swap`:** [2](#0-1) 

The pool unconditionally passes its own `msg.sender` as the first argument to `_beforeSwap`. When the call originates from the router, `msg.sender` is the router address.

**The router calls `pool.swap()` directly — `MetricOmmSimpleRouter.exactInputSingle`:** [3](#0-2) 

The router does not forward the originating user's address to the pool in any position the extension can inspect. The end user's address is stored only in transient callback context (`_setNextCallbackContext`) for payment purposes, not surfaced to the extension.

**Contrast with `DepositAllowlistExtension` — which does not share this flaw:** [4](#0-3) 

`beforeAddLiquidity` ignores `sender` (first param, the direct caller) and checks `owner` (second param), which the pool populates from the caller-supplied `owner` argument — the actual position owner regardless of who the direct caller is.

**Full exploit call chain:**
```
User (not allowlisted) → Router.exactInputSingle()
  → Pool.swap()          [pool.msg.sender = router]
  → _beforeSwap(sender = router, ...)
  → Extension.beforeSwap(sender = router, ...)
  → allowedSwapper[pool][router] == true  ← checks router, not user
  → passes; swap executes
```

## Impact Explanation

This is an admin-boundary break. A pool admin who deploys a curated pool with `SwapAllowlistExtension` faces an inescapable dilemma: if the router is not allowlisted, no user can reach the pool via the router; if the router is allowlisted (the only way to enable the standard UX path for legitimate users), every user — including those explicitly not allowlisted — can bypass the access control by calling `router.exactInputSingle(...)`. The pool admin's intended invariant ("only allowlisted addresses may swap") is fully defeated by an unprivileged public path. This matches the allowed impact category: **admin-boundary break via an unprivileged path**.

## Likelihood Explanation

Medium. The bypass requires the router to be on the allowlist. Pool admins who want their allowlisted users to be able to use the router (the standard, expected UX) will naturally add the router to the allowlist, inadvertently enabling the bypass for all users. The router is a public, permissionless contract callable by anyone.

## Recommendation

The extension must check the **economically relevant actor** — the end user — not the direct caller of `pool.swap()`.

1. **Pass the original user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between the router and the extension.
2. **Align with the deposit allowlist pattern**: Introduce a separate "originator" field in the swap hook signature (analogous to `owner` in `addLiquidity`) that the router fills with the end user's address, and have the extension check that field instead of `sender`.

## Proof of Concept

```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension
SwapAllowlistExtension ext = new SwapAllowlistExtension(factory);
// Pool deployed with ext as beforeSwap extension

// Admin allowlists the router so that allowed users can use it
ext.setAllowedToSwap(pool, address(router), true);

// Disallowed user (not individually allowlisted) bypasses the guard:
// router.msg.sender = disallowedUser
// pool.msg.sender   = router  ← extension sees this
router.exactInputSingle(ExactInputSingleParams({
    pool:      pool,
    recipient: disallowedUser,
    zeroForOne: true,
    amountIn:  1e18,
    ...
}));
// Extension checks allowedSwapper[pool][router] == true → passes
// Disallowed user successfully swaps on the curated pool
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
