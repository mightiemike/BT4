Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the pool's `msg.sender`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted user can bypass the guard by calling through the router.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap(), not the originating user
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap()` from the router's address. [5](#0-4) 

The result is a binary, unresolvable configuration trap for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | **Every** user — including non-allowlisted ones — bypasses the guard |

There is no configuration that correctly restricts router-mediated swaps to only the intended set of users. The wrong value is `allowedSwapper[pool][router]` being evaluated where `allowedSwapper[pool][user]` is required.

## Impact Explanation

When the pool admin allowlists the router (the natural and expected action to enable router-mediated swaps for permitted users), the `SwapAllowlistExtension` guard is fully bypassed for all router-mediated swaps. Any unpermitted address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps on a pool configured to be restricted. This constitutes an admin-boundary break: an unprivileged public path (the router) circumvents the configured allowlist guard, breaking the core pool access-control invariant.

## Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router address, which is the natural and expected action when the admin wants allowlisted users to be able to use the standard router. The admin has no mechanism to distinguish "router called by an allowlisted user" from "router called by anyone" at the extension level, making the bypass an inevitable consequence of enabling router access. The attacker requires no special privileges — any EOA can call the public router.

## Recommendation

The pool should forward the original initiating user as `sender` to extensions rather than the immediate `msg.sender`. One approach: require the router to pass the user's address via `extensionData`, and have the extension decode and verify it against a registry of trusted routers (so only factory-registered routers can supply a trusted `sender`). Alternatively, the pool can expose a `swapWithSender(address trustedSender, ...)` entry point restricted to factory-registered routers, forwarding `trustedSender` to hooks instead of `msg.sender`. The `DepositAllowlistExtension` should be audited for the analogous payer/owner separation issue in `MetricOmmPoolLiquidityAdder`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on the `beforeSwap` hook.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — intending to allow router-mediated swaps for permitted users.
3. Non-allowlisted `attacker` calls `router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}))`.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` to the pool is the router address.
5. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → returns selector, no revert.
7. Attacker's swap executes on the restricted pool despite never being individually allowlisted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```
