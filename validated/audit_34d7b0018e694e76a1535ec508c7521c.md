Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as `sender` Instead of Actual End-User, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool — the router contract — not the actual end-user. When `MetricOmmSimpleRouter` is allowlisted (the natural operational setup), any unprivileged user can bypass the per-user swap allowlist entirely by routing through `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via MetricOmmSimpleRouter
    recipient,
    ...
);
```

`_beforeSwap()` forwards this value directly to the extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without encoding the actual end-user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData   // end-user address is NOT encoded here
    );
```

So the pool sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`. The actual end-user who called the router is never checked. If the pool admin allowlists the router (the natural operational setup), every user on the network bypasses the per-user allowlist by routing through `MetricOmmSimpleRouter`. A direct call from an un-allowlisted address to `pool.swap()` is correctly blocked, but the router path is not.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified users, institutional counterparties) provides no actual restriction once the router is allowlisted. Any arbitrary address can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, have the router execute `pool.swap()` on their behalf, and the extension passes because it sees the allowlisted router address. The pool's liquidity is exposed to all swappers, directly contradicting the pool admin's access-control intent and potentially enabling unauthorized extraction of LP assets at oracle-anchored prices — a direct loss of LP principal.

## Likelihood Explanation
Likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who configures `SwapAllowlistExtension` and also wants users to swap through the official router must allowlist the router — at which point the allowlist is fully bypassed for all users. The bypass requires no special privileges, no malicious setup, and no non-standard tokens: any EOA can call the router.

## Recommendation
The `sender` passed to `beforeSwap` must represent the actual end-user, not the intermediary. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode `msg.sender` (the actual end-user) into `extensionData` so that extensions can read the true initiator.
2. **Extension-side**: `SwapAllowlistExtension.beforeSwap()` should decode and check the actual user from `extensionData` when `sender` is a known router/intermediary, or the protocol should define a standard `extensionData` field for the "true initiator" that all periphery contracts populate.
3. **Alternatively**: The pool could expose a `swapFor(address user, ...)` entry point that passes `user` as `sender` to extensions, analogous to how `addLiquidity(owner, ...)` correctly separates the payer (`msg.sender`) from the economic actor (`owner`).

## Proof of Concept
```
Setup:
  - Pool configured with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  1. Alice (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: alice, ...})

  2. Router calls pool.swap(alice, zeroForOne, amount, ...)
     // msg.sender = router (allowlisted)

  3. Pool calls _beforeSwap(router, alice, ...)
     // sender = router

  4. Extension checks:
       allowedSwapper[pool][router] == true → passes

  5. Alice's swap executes successfully despite not being allowlisted.

Direct call (correctly blocked):
  1. Alice calls pool.swap(...) directly
  2. Extension checks allowedSwapper[pool][alice] == false → reverts NotAllowedToSwap
```