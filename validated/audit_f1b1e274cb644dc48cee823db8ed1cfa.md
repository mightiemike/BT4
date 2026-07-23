Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any caller to bypass per-pool swap allowlists via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap`. When `MetricOmmSimpleRouter` is used, the router contract becomes that direct caller. Because the router has no access control of its own, allowlisting the router — which is required for any router-mediated swap to succeed — grants every address on the network the ability to bypass the per-user allowlist entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` (via `ExtensionCalling`) forwards that value unchanged as the first argument to every configured extension, including `SwapAllowlistExtension.beforeSwap`.

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call: [3](#0-2) 

`exactInputSingle` has no access control — any address may call it: [4](#0-3) 

This creates an inescapable dilemma for the pool admin:
- Router **not** allowlisted → all router-mediated swaps revert, even for individually allowlisted users.
- Router **allowlisted** → every address on the network can swap through the router, bypassing the per-user allowlist entirely.

No configuration simultaneously allows allowlisted users to use the router while blocking non-allowlisted users.

## Impact Explanation

The swap allowlist is the primary access-control mechanism for restricted pools (e.g., KYC-gated or institutional-only pools). When the router is allowlisted — the only way to enable router-mediated swaps — any unprivileged user can execute swaps against the pool at oracle-derived prices. This constitutes a direct loss of LP assets and user principal above Sherlock thresholds, and is an admin-boundary break: an unprivileged path bypasses the access control the pool admin configured.

## Likelihood Explanation

High. The router is the standard entry point for end-users. Any pool admin who deploys a `SwapAllowlistExtension` and wants users to be able to use the router must allowlist it. Once the router is allowlisted, the bypass is unconditional and requires no special setup from the attacker — a single `exactInputSingle` call suffices.

## Recommendation

The extension must verify the **originating user**, not the direct pool caller. Two sound approaches:

1. **Forward the originating user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it against `allowedSwapper`. This requires a trusted router or a signed payload.
2. **Check both router and originating user**: Require that either (a) `sender` itself is allowlisted, or (b) `sender` is a trusted router AND the user address forwarded in `extensionData` is allowlisted.

The simplest safe fix is to remove router-level allowlisting and instead require the router to forward the originating user in `extensionData`, then check that address in the extension.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only allowed swapper
  allowedSwapper[pool][router] = true  // required for router-mediated swaps

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, tokenIn: token0, ...})

  Execution trace:
    router.exactInputSingle()           // msg.sender = bob
      pool.swap(recipient, ...)         // msg.sender = router
        _beforeSwap(router, ...)
          SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  → passes
        swap executes, bob receives token1

Result: bob swaps successfully on an allowlist-restricted pool.
        alice's exclusive access is violated.
        LP funds flow to an unauthorized counterparty.
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
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
