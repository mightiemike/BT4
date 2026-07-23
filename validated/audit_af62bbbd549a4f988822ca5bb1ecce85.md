Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` inside `MetricOmmPool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool sees the router contract as `msg.sender`, not the originating user. If a pool admin allowlists the router to enable router-mediated swaps for permitted users, every unpermitted address can bypass the allowlist by calling through the router, executing swaps at oracle prices against LP positions that were configured to reject them.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded value: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` seen by the pool: [3](#0-2) 

The same identity collapse occurs in the multi-hop `exactOutput` callback path, where `_exactOutputIterateCallback` calls `pool.swap()` from inside the callback: [4](#0-3) 

The structural mismatch is:

| Call path | `sender` seen by extension | Allowlist entry required |
|---|---|---|
| `user → pool.swap()` | `user` | `allowedSwapper[pool][user]` |
| `user → router → pool.swap()` | `router` | `allowedSwapper[pool][router]` |

Once `allowedSwapper[pool][router] = true` is set (the natural admin action to enable router-mediated swaps for permitted users), the check `allowedSwapper[msg.sender][sender]` resolves to `true` for every caller regardless of whether they are individually allowlisted. The `allowAllSwappers` bypass path is a separate flag and does not mitigate this; the `allowedSwapper` path is the broken one. No existing guard in the extension or the pool prevents this collapse.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is a restricted venue: only approved counterparties may trade against its oracle-priced liquidity. Bypassing the guard lets any unpermitted address execute swaps at the oracle bid/ask, extracting value from LP positions at prices the LPs agreed to offer only to vetted counterparties. This is a direct loss of LP principal — the pool settles real token transfers at oracle prices for trades it was configured to reject. This meets the contest threshold for a High severity finding under the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" and "direct loss of user principal" impact categories.

## Likelihood Explanation
The trigger is a single, natural admin action: allowlisting the router so that permitted users can access the pool through the standard periphery. Any pool that (a) has `SwapAllowlistExtension` active and (b) has added the router to its allowlist is immediately exploitable by any address. The router is a public, permissionless contract; no special capability is required beyond calling it. The bypass is reachable through all four public entry points of `MetricOmmSimpleRouter`: `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

## Recommendation
The extension must gate the originating user, not the immediate pool caller. Two sound approaches:

1. **Pass the original user through the router.** Add an `originSender` field to the router's swap parameters and have the pool forward it as a separate argument to extensions, or encode it in `extensionData` with a signature the extension can verify.
2. **Check `tx.origin` only as a last resort** (acceptable in a tightly scoped allowlist context where flash-loan and contract-wallet risks are already accepted by the pool admin).

Until fixed, pool admins must choose between (a) not allowlisting the router — blocking all router-mediated swaps for everyone — or (b) allowlisting the router and accepting that the allowlist is effectively disabled.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Admin calls setAllowedToSwap(pool, alice, true)       // alice is permitted
  - Admin calls setAllowedToSwap(pool, router, true)      // router added so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — msg.sender in pool = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true  → passes
  - Bob's swap executes at oracle price, draining LP value

Verification:
  - Direct call: bob calls pool.swap() directly → sender=bob → allowedSwapper[pool][bob]==false → reverts
  - Router call: bob calls router.exactInputSingle() → sender=router → allowedSwapper[pool][router]==true → succeeds
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
