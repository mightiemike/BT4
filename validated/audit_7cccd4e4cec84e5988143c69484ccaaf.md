Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, enabling full allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which is `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` is the caller, `sender` is the router address. A pool admin who allowlists the router (the expected configuration for normal UX) simultaneously grants swap access to every non-allowlisted user who routes through it, completely nullifying the per-user access control the extension is designed to enforce.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` — the direct caller of `swap()` — as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool — not the end user: [3](#0-2) 

The same pattern holds for `exactInput` (all hops): [4](#0-3) 

And for `exactOutputSingle` and the recursive hops inside `_exactOutputIterateCallback`: [5](#0-4) [6](#0-5) 

In every router entry point, the `sender` received by the extension is the router address, not the actual end user. The `allowedSwapper` mapping is keyed by `[pool][sender]`, so `allowedSwapper[pool][router] == true` passes the check for any caller of the router, regardless of whether that caller is individually allowlisted.

The two broken invariants are:
- If the router is **not** allowlisted: allowlisted users are also blocked when they use the router (router address fails the check).
- If the router **is** allowlisted: every non-allowlisted user can swap through the router — full bypass.

The pool admin cannot simultaneously (a) let allowlisted users use the router and (b) block non-allowlisted users from using the router.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (e.g., KYC-verified addresses, whitelisted market makers, or private LP partners) is fully bypassable by any unprivileged user who calls `MetricOmmSimpleRouter`. The attacker executes swaps at oracle-derived bid/ask prices in a pool that was explicitly configured to deny them access. The access-control boundary the pool admin paid to enforce is completely nullified, and the pool is exposed to any counterparty the admin intended to exclude. This constitutes a broken core pool functionality / admin-boundary break meeting contest threshold.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool admin who wants allowlisted users to have a normal UX must allowlist the router, which simultaneously opens the pool to all users. The bypass is reachable through a routine, expected configuration step. No privileged key beyond the pool admin's own allowlist setter is required, and the attacker needs only to call the public router.

## Recommendation

The extension must gate on the economically relevant actor — the end user — not the direct caller of `pool.swap()`. Three complementary approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` forward `msg.sender` as an authenticated field inside `extensionData`, and have `SwapAllowlistExtension` decode and check that field when the caller is a known router.

2. **Check `recipient` as a proxy for the end user** (simpler but less precise): the router always sets `recipient` to the user-supplied address; the extension could check `recipient` instead of `sender` when `sender` is a known router address.

3. **Document and enforce incompatibility** at the factory level by preventing pools that use `SwapAllowlistExtension` from being accessed through the router.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(bob, ...)          // msg.sender = router
        → _beforeSwap(router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ PASS
      → swap executes at oracle price
      → bob receives output tokens

Result: bob, a non-allowlisted address, successfully swaps in a pool
        that was configured to deny him access.
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
