Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any Caller to Bypass a Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the pool's `msg.sender`, so the extension sees the router address as the swapper identity. If the pool admin allowlists the router to permit legitimate users, every unpermissioned user can bypass the allowlist by calling through the same public router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same pattern applies in `exactInput` (all hops): [4](#0-3) 

And in `exactOutputSingle`: [5](#0-4) 

For `exactOutput` multi-hop, inner hops are triggered from inside `_exactOutputIterateCallback` where `msg.sender` is the outer pool, so the inner pool's extension sees the outer pool address as the swapper — an address almost certainly not in any allowlist: [6](#0-5) 

The pool admin faces an impossible choice: not allowlisting the router breaks legitimate router usage; allowlisting the router silently voids the allowlist for all users. No existing guard in `SwapAllowlistExtension` or the router recovers the original user's identity.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise curated addresses is fully bypassable by any unpermissioned user calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle`. The attacker receives the same swap execution as an allowlisted direct caller — same oracle price, same bins, same output tokens. The allowlist guard is silently voided for the entire pool, allowing unrestricted token extraction from LP positions at oracle-fair prices. This constitutes a broken core pool security feature causing direct loss of the curation guarantee the pool admin enforced, meeting the "admin-boundary break by an unprivileged path" and "broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary documented user-facing swap entrypoint. Any user who calls the router by default — without any special privilege, custom contract, or unusual token behavior — bypasses the guard. The bypass requires a single standard router call and is repeatable indefinitely. The only precondition is that the pool admin has allowlisted the router (which is required for any legitimate router usage), making exploitation automatic once the pool is in production use.

## Recommendation

The pool must convey the original economic actor's identity to the extension. Two sound approaches:

1. **Pass the original initiator through `extensionData`**: Require the router to ABI-encode `msg.sender` (the user) into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and verify it. This requires a convention between router and extension but correctly identifies the originating user regardless of routing depth.

2. **Check `recipient` instead of `sender`**: For swap allowlists where the intent is to restrict who receives output tokens, changing the extension to gate on `recipient` (the second argument) would correctly identify the beneficiary regardless of routing path, provided the pool admin's intent aligns with restricting token recipients.

The cleanest long-term fix is option 1 with a standardized `extensionData` header that periphery contracts always populate with the originating user address, so extensions can rely on it across all router entry points.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that legitimate users can use the router)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curated_pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     })
  2. Router calls pool.swap(attacker, true, X, ...) — router is msg.sender
  3. Pool calls _beforeSwap(router, attacker, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; attacker receives output tokens at oracle price
  6. Allowlist bypassed; attacker was never individually permitted
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router, call `exactInputSingle` from an address not in the allowlist, assert the swap succeeds and the attacker receives output tokens.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
