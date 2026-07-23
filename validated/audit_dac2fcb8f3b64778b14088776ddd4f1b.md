Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, permanently blocking all EOA swaps or fully bypassing the allowlist — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `sender` — which is `msg.sender` of `pool.swap()`, i.e., the router contract — against the per-pool allowlist. Because `MetricOmmPool.swap` unconditionally calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle, EOAs cannot call the pool directly and must route through `MetricOmmSimpleRouter`. This produces two mutually exclusive failure modes: either all EOA swaps revert (router not allowlisted) or the allowlist is completely nullified (router allowlisted), with no configuration that avoids both.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` (via `ExtensionCalling`) forwards that value unchanged as the first argument to `IMetricOmmExtensions.beforeSwap`. `SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the per-pool allowlist: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is `msg.sender` of `pool.swap()`: [3](#0-2) 

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end user's address is never consulted.

EOAs cannot call `pool.swap()` directly because the pool unconditionally calls `IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...)` to settle the swap: [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the LP position owner, second argument) rather than `sender` (the adder contract, first argument): [5](#0-4) 

`SwapAllowlistExtension` has no equivalent design — it checks `sender` (the router) rather than the economically relevant actor (the user).

## Impact Explanation

Two mutually exclusive fund-impacting failure modes arise:

**Mode A — Permanent swap lock:** Pool admin allowlists specific user addresses via `setAllowedToSwap(pool, user, true)` but does not add the router. Every EOA swap through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap` because `allowedSwapper[pool][router]` is `false`. Since EOAs cannot implement the swap callback, they have no alternative path. The pool's swap functionality is permanently broken for all EOA users, trapping LP principal in a pool that cannot be traded against — broken core pool functionality causing loss of funds or unusable swap flows.

**Mode B — Full allowlist bypass:** To unblock users, the pool admin adds the router: `setAllowedToSwap(pool, router, true)`. Now any address — including those the admin explicitly never allowlisted — can swap through the router. The allowlist guard is completely nullified — an admin-boundary break where the pool admin's access control invariant is bypassed by any unprivileged caller.

## Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with `allowAllSwappers = false` and expects users to swap through `MetricOmmSimpleRouter` hits Mode A immediately on the first router-mediated swap. The pool admin's only remediation option (adding the router) triggers Mode B. No special privilege is required — any allowlisted EOA attempting a normal swap is sufficient to trigger Mode A, and any non-allowlisted EOA can exploit Mode B once the router is added.

## Recommendation

Mirror the `DepositAllowlistExtension` pattern: gate the economically relevant actor, not the intermediary. For swaps, the relevant actor is the end user who initiated the transaction. One approach is to pass the original initiator through `extensionData` (set by the router) and verify it in the extension. Alternatively, the extension could decode the true initiator from `extensionData` when `sender` is a recognized router, and fall back to checking `sender` directly for non-router callers. A minimal fix would be for `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData`, and for `SwapAllowlistExtension.beforeSwap` to decode and check that value when present.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is allowlisted.
3. Alice calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient=alice, ...)` — `msg.sender` of `pool.swap()` = router address.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `false`.
7. Extension reverts `NotAllowedToSwap`. Alice's swap fails despite being explicitly allowlisted.

To demonstrate Mode B:
- Pool admin calls `setAllowedToSwap(pool, router, true)`.
- Bob (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
- Extension evaluates `allowedSwapper[pool][router]` → `true`.
- Bob's swap succeeds — the allowlist is bypassed entirely.

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

**File:** metric-core/contracts/MetricOmmPool.sol (L257-263)
```text
      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
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
