Audit Report

## Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual Swapper, Allowing Any User to Bypass a Curated Pool's Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the end user. This means the allowlist gates the router address rather than the actual swapper, producing a two-sided failure: legitimate allowlisted users are blocked when using the router, and if the admin allowlists the router to fix this, every unpermissioned address can bypass the curated pool's access control by routing through `MetricOmmSimpleRouter`.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // pool's msg.sender — the router when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, recipient, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without forwarding the original caller:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The actual user's address (`msg.sender` of the router) is stored only in transient storage for the payment callback — it is never passed to the pool's `swap` function. The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181).

The `onlyPool` guard on the extension only verifies the caller is a registered pool; it does not verify which user initiated the action:

```solidity
// metric-periphery/contracts/extensions/base/BaseMetricExtension.sol L19-23
modifier onlyPool() {
  if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
    revert OnlyPool(msg.sender, FACTORY);
  }
  _;
}
```

No existing guard recovers the original end-user address from transient storage or any other source before the allowlist check executes.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` is a curated pool where only specific counterparties are permitted to trade. The broken invariant is `allowedSwapper[pool][actualUser]` — the extension checks `allowedSwapper[pool][router]` instead. If the router is allowlisted (the only way to let legitimate users use the standard periphery path), every unpermissioned address can trade in the pool by routing through `MetricOmmSimpleRouter`. LP assets are exposed to swappers the admin explicitly intended to exclude, and the pool's oracle-anchored pricing can be consumed by unauthorized parties. This is a direct admin-boundary break: an unprivileged path (calling the public router) bypasses the pool admin's access control policy, with direct fund-impacting consequences for LPs.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint for the protocol. Any pool operator who deploys a curated pool and wants their allowlisted users to use the standard router must allowlist the router address, which immediately opens the bypass to all users. The trigger requires no special privilege: any EOA can call `exactInputSingle` on the router pointing at the curated pool. The condition is reachable on every curated pool deployment that intends to support router-based access.

## Recommendation

Pass the original end-user address through the call chain rather than the intermediary's address. The cleanest fix is for the pool's `swap` interface to accept an explicit `swapper` address that the router populates with its own `msg.sender` before forwarding to the pool, and for `SwapAllowlistExtension.beforeSwap` to gate on that field instead of the raw `sender`. Alternatively, the router can pass the originating user address as part of `extensionData`, and the extension can decode and check it — though this requires the extension to trust the router's encoding. A third option is for `SwapAllowlistExtension` to maintain a registry of trusted routers and, when `sender` is a known router, skip the per-user check or require the user address to be supplied via `extensionData`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    // Required so that legitimate users can use MetricOmmSimpleRouter
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: curatedPool, ...})
  - Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  - Attacker swaps successfully in a pool they were never permitted to access

Broken invariant:
  - allowedSwapper[pool][attacker] == false, but the swap succeeds
  - The wrong value checked: allowedSwapper[pool][router] instead of allowedSwapper[pool][attacker]
```