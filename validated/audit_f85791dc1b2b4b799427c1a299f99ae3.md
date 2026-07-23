Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User on Router-Mediated Swaps — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` binds to its own `msg.sender`. When `MetricOmmSimpleRouter` mediates a swap, the router becomes the pool's `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Any pool that allowlists the router to support standard periphery access inadvertently grants every non-allowlisted user the ability to bypass the gate by routing through `MetricOmmSimpleRouter`.

## Finding Description

`MetricOmmPool.swap` hardcodes `msg.sender` as the `sender` argument forwarded to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap
    recipient,
    ...
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against the allowlist:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool without passing the real user:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // ← recipient, not sender
    params.zeroForOne,
    ...
);
``` [3](#0-2) 

The pool's `msg.sender` is the router, so `sender = router` reaches the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][alice]`. The same misbinding applies to every hop after the first in `exactInput`, where `address(this)` (the router) is used as payer: [4](#0-3) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the explicit `owner` argument — a separate parameter the pool always sets to the real position owner — rather than the pool's `msg.sender`: [5](#0-4) 

The swap path has no analogous explicit-actor parameter; the pool's `swap` signature only accepts `recipient`, not a declared economic actor: [6](#0-5) 

## Impact Explanation

Two fund-impacting outcomes arise from this misbinding:

1. **Allowlist bypass (critical path):** A pool admin allowlists the router so that legitimate users can reach the pool through the standard periphery. Because the extension sees `sender = router`, every non-allowlisted user can swap by routing through `MetricOmmSimpleRouter`. The curated-pool invariant is broken: unauthorized principals trade in a pool designed to exclude them, draining LP value or violating institutional constraints. This is an admin-boundary break via an unprivileged path.

2. **Broken swap path (secondary path):** If the admin does not allowlist the router, every individually-allowlisted user is silently blocked from using the router. Core swap functionality is unusable for the intended audience.

## Likelihood Explanation

Any pool deploying `SwapAllowlistExtension` that also wants to support the standard router must allowlist the router, automatically triggering scenario 1. No special privilege is required — any EOA can call `exactInputSingle`. The router is the primary user-facing entry point, making this collision expected in normal operation.

## Recommendation

Mirror the pattern used by `addLiquidity` / `DepositAllowlistExtension`: add an explicit `sender` parameter to the pool's `swap` function so the caller can declare the economic actor. The router would pass `msg.sender` (the real user) as that argument. The pool forwards it to `_beforeSwap`, and the extension checks the declared sender rather than the pool's `msg.sender`.

```solidity
// Pool swap signature (proposed)
function swap(
    address sender,      // ← new: declared economic actor
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
) external returns (int128, int128);

// Router exactInputSingle (proposed)
IMetricOmmPoolActions(params.pool).swap(
    msg.sender,          // ← actual user, not router
    params.recipient,
    ...
);
```

As a short-term mitigation without changing the pool interface, the extension can decode the real sender from `extensionData`, with the router encoding `msg.sender` there.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to let any user reach the pool through the router.
3. Pool admin does **not** call `setAllowedToSwap(pool, alice, true)`.
4. Alice (non-allowlisted) calls `router.exactInputSingle({pool, recipient: alice, …})`.
5. Router calls `pool.swap(alice_as_recipient, …)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(sender=router, recipient=alice, …)` then `extension.beforeSwap(sender=router, …)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Alice receives output tokens despite never being allowlisted.

The allowlist guard is fully bypassed for every non-allowlisted user who routes through `MetricOmmSimpleRouter`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
