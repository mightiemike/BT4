Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Swap Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the end user. Any pool admin who allowlists the router (the necessary step to let allowlisted users use the router) inadvertently grants every unprivileged address the ability to bypass the swap gate by calling any router entry point.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first positional argument to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is whatever address called the pool's `swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(...)` directly, making the router contract the pool's `msg.sender`: [4](#0-3) 

The pool therefore passes the router address as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. This creates an irresolvable dilemma:

- **Router not allowlisted**: every allowlisted user is blocked from using the router — core swap functionality is broken for the intended audience.
- **Router allowlisted** (the natural fix): `allowedSwapper[pool][router] == true`, so the guard passes for every caller regardless of whether the actual end user is on the allowlist.

No existing guard in the extension, pool, or router checks the originating `msg.sender` of the router call. The `extensionData` field passed through the router is caller-controlled and not validated by the extension. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-gated, institutional-only, or regulatory-compliant participants) can be freely traded by any address via the public router once the router is allowlisted. The pool's access-control invariant is silently voided. Unauthorized parties can extract liquidity at privileged pricing, drain one-sided bins, or interact with pools contractually restricted to specific counterparties — constituting direct loss of LP assets or unauthorized fund flows. This is a broken core pool functionality / admin-boundary break with direct fund impact above Sherlock thresholds.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary public swap interface. Any pool admin who enables `SwapAllowlistExtension` and also wants allowlisted users to use the router must allowlist the router address — this is the obvious and expected configuration. The bypass is reachable by any unprivileged user on every such pool with no special preconditions, no privileged access, and no non-standard token behavior required.

## Recommendation
The extension must gate on the actual end user, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: require the router to encode the real user address in `extensionData` and have the extension decode and verify it (with a pool-level flag requiring this encoding so it cannot be omitted).
2. **Check `recipient` instead of `sender`**: the extension could check `allowedSwapper[pool][recipient]` instead of `allowedSwapper[pool][sender]`, since the router already sets `recipient` to the actual user via `params.recipient`. This is simpler but requires the pool admin to understand the semantic shift.

## Proof of Concept
```
1. Pool admin deploys a pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowed.
3. Admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({pool, recipient: bob, ...}).
5. Router calls pool.swap(bob, ...) → pool passes msg.sender=router as sender to _beforeSwap.
6. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
7. Bob's swap executes successfully despite never being allowlisted.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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
