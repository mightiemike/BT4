Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. `MetricOmmPool.swap` passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This creates a binary failure: either router-mediated swaps are blocked for all allowlisted users, or the allowlist is completely bypassed for any user who routes through the public router.

## Finding Description

**Root cause — pool passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.swap`, the call to `_beforeSwap` uses `msg.sender` as the first argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument in the ABI-encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whatever the pool passed — the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

**Router call path — `msg.sender` inside `pool.swap` is the router, not the end user:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` seen by the pool: [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Binary failure mode:**

The allowlist is keyed `allowedSwapper[pool][swapper]`: [6](#0-5) 

- **Case 1 — router not allowlisted:** `allowedSwapper[pool][router]` is `false`. Every router-mediated swap reverts with `NotAllowedToSwap()`, even for individually allowlisted users. Core swap functionality is broken for the intended user set.
- **Case 2 — admin allowlists the router to fix Case 1:** `allowedSwapper[pool][router]` becomes `true`. Now any unpermissioned user can call `MetricOmmSimpleRouter` and swap against the curated pool. The allowlist is fully bypassed.

No existing guard prevents this. The `allowAllSwappers` short-circuit only helps if the pool admin intentionally opens the pool to everyone, which defeats the purpose of the extension.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is explicitly curated — the admin intends to restrict who can trade against it. The bypass allows any unpermissioned user to execute swaps against the pool via the public router. Depending on pool configuration (e.g., a pool restricted to a single market maker whose pricing assumptions depend on exclusive access), this can result in direct loss of LP principal through trades the pool was designed to reject. This meets **Admin-boundary break: unprivileged path bypasses a required guard** and **Broken core pool functionality causing loss of funds**.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it. No special setup is required beyond knowing the pool address. The bypass is triggered by the normal, documented user flow (routing through the periphery router). The pool admin cannot prevent this without removing the extension entirely, since allowlisting the router to restore functionality for legitimate users simultaneously opens the pool to everyone.

## Recommendation

The pool must pass the original end-user's address — not `msg.sender` — as the `sender` argument to `_beforeSwap`. Two approaches:

1. **Transient storage context:** Store the original `msg.sender` in transient storage at the top of `MetricOmmPool.swap` and read it inside `ExtensionCalling._beforeSwap` when constructing the hook call. The protocol already uses EIP-1153 transient storage for reentrancy guards and callback context, making this pattern established.
2. **Explicit originator parameter:** Add an `originator` parameter to the public `swap` entry point and require the router to pass `msg.sender` explicitly, then forward it through `ExtensionCalling` to the hook.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT allowlist the router or bob.

Attack (bob bypasses allowlist):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(msg.sender=router, ...).
  3. Pool calls _beforeSwap(sender=router, ...) → extension.beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → false → Reverts.
     (Case 1: legitimate router users also blocked.)

Fix attempt by admin (makes it worse):
  5. Pool admin calls setAllowedToSwap(pool, router, true) to allow alice to use the router.
  6. Now bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  7. Extension checks allowedSwapper[pool][router] → true.
  8. bob's swap executes successfully against the curated pool.
     → Allowlist fully bypassed for any user who routes through the router.

Foundry test outline:
  - deployPool with SwapAllowlistExtension
  - setAllowedToSwap(pool, router, true)
  - prank(bob) → router.exactInputSingle(pool, ...)
  - assert swap succeeds despite bob not being individually allowlisted
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
