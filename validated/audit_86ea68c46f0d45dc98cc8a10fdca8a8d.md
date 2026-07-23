Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. Any pool admin who allowlists the router to enable router-mediated swaps for approved users inadvertently opens the pool to every user, completely defeating the allowlist guard.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is on the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly, making the router itself `msg.sender` inside `pool.swap`: [4](#0-3) 

The same pattern applies to `exactInput` (multi-hop) and `exactOutputSingle`/`exactOutput`: [5](#0-4) 

The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The router is a single shared address; the extension cannot distinguish which end user initiated the call. No existing guard in `SwapAllowlistExtension` or `ExtensionCalling` recovers the original `msg.sender` from `extensionData` or any other source.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to be a private or restricted venue — only explicitly approved addresses may swap. Once the router is allowlisted (the only way to support router-mediated swaps for approved users), every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`. This is a direct, complete break of the access-control invariant the pool admin enforced, exposing LP capital to price impact from counterparties the admin explicitly excluded. This matches the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" allowed impact category.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard user-facing entry point. Any pool using `SwapAllowlistExtension` that also wants to support router-mediated swaps for its approved users must allowlist the router — there is no other mechanism. That is the expected production configuration, not an edge case. Any user who discovers the router is allowlisted can immediately and repeatedly exploit it with zero special privileges.

## Recommendation
The extension must receive the original end-user identity, not the intermediary's address. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should accept an `originSender` parameter and forward it inside `extensionData` so extensions can read the true initiator.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap` should decode the original sender from `extensionData` when present, falling back to the raw `sender` argument for direct (non-router) calls.

Alternatively, the pool can gate on `recipient` instead of `sender` when the design intent is to control who receives output tokens, but the cleanest fix is to thread the original `msg.sender` through the router into `extensionData`.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   (alice is the only approved swapper)
  allowedSwapper[pool][router] = true   (admin adds router so alice can use it)

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, ...)   // msg.sender inside pool.swap = router address

  SwapAllowlistExtension.beforeSwap receives:
    sender = router address
    allowedSwapper[pool][router] == true  → check passes

  Result: bob swaps successfully in a pool he was never approved for.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
