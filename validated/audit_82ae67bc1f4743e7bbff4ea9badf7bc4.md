Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Full Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` — the immediate caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the allowlist check becomes `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. Allowlisting the router (required for any router-mediated swap) grants every user on the network the ability to bypass the per-address allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks that `sender` against the per-pool allowlist: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` inside the pool: [3](#0-2) 

The same pattern applies to `exactOutputSingle` and `exactInput`: [4](#0-3) 

The allowlist mapping is keyed `allowedSwapper[pool][swapper]`: [5](#0-4) 

When the router is allowlisted (the only way to support normal router usage), `allowedSwapper[pool][router] = true`, and the check passes for every user who calls through the router, regardless of whether that user is individually allowlisted. There is no secondary check on the originating user.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers). Once the router is allowlisted, any unprivileged user can execute swaps in that pool by calling the router. This is a direct, complete bypass of the access-control invariant the pool admin enforced, exposing LP funds to trades from actors the pool was explicitly designed to exclude. This constitutes broken core pool functionality causing loss of funds and an admin-boundary break reachable by any unprivileged user.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, publicly deployed periphery contract that ordinary users are expected to use. Any user who discovers the bypass can exploit it immediately with no special privileges, no malicious setup, and no admin cooperation. The router is a fixed, known address, so the bypass is permanently available once the router is allowlisted.

## Recommendation

The extension must gate the economically relevant actor, not the immediate caller of `pool.swap`. The cleanest fix is to have the router encode `msg.sender` (the actual user) into `extensionData` before calling the pool, and have `SwapAllowlistExtension.beforeSwap` decode and check that address instead of `sender`. This requires a coordinated encoding convention between the router and the extension. Alternatively, if `recipient` is guaranteed to be the actual beneficiary in all router configurations, the extension can check `recipient` instead of `sender`, though this does not hold for multi-hop intermediate hops where the router itself is the recipient.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router use
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)

Attack:
  - Alice (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  - Router calls pool.swap(recipient=alice, ...)
  - Pool passes msg.sender (= router) as `sender` to _beforeSwap
  - Extension checks allowedSwapper[pool][router] → true
  - Swap executes; Alice receives output tokens

Expected: revert NotAllowedToSwap
Actual:   swap succeeds; allowlist is bypassed
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
