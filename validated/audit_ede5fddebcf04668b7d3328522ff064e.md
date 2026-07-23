Audit Report

## Title
SwapAllowlistExtension Allowlist Bypass via MetricOmmSimpleRouter — Any User Can Swap on Curated Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender` (i.e., whoever called `pool.swap()`). When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router address, so the extension checks `allowedSwapper[pool][router]` rather than the actual user. If the pool admin allowlists the router to support legitimate router-mediated swaps, any unprivileged user can bypass the allowlist entirely by routing through the public, permissionless router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // pool's msg.sender = whoever called pool.swap()
  ...
```

`ExtensionCalling._beforeSwap` faithfully forwards this `sender` value with no mechanism to carry the original user identity through a router hop:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The router does not encode the original `msg.sender` into `extensionData` or any other parameter. The pool's `msg.sender` is the router, so the extension receives `sender = router`. The check becomes `allowedSwapper[pool][router]`. For any allowlisted user to use the router on a curated pool, the admin must allowlist the router address. Once the router is allowlisted, the check passes for every caller of the router regardless of identity, because the router is a public, permissionless contract. The admin faces an inescapable dilemma: not allowlisting the router breaks periphery usability for legitimate users; allowlisting the router opens the pool to all callers.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers). Once the router is allowlisted to support normal periphery usage, any unprivileged address can trade on the pool by calling the public router. This breaks the core invariant that the allowlist enforces: "only approved addresses may swap." Unauthorized swaps drain LP assets at oracle-derived prices, constituting a direct loss of LP-owed value and broken core pool functionality — a High-severity impact under Sherlock contest thresholds.

## Likelihood Explanation
The bypass requires no special privileges, no flash loans, and no complex setup. Any user who can call `MetricOmmSimpleRouter.exactInputSingle` (a public, payable function) with the target pool address can execute the bypass in a single transaction. The only precondition is that the pool admin has allowlisted the router — which is the expected operational configuration for any pool that intends to support router-mediated swaps for its allowlisted users.

## Recommendation
The `SwapAllowlistExtension` must gate the original user, not the intermediary. The preferred fix is extension-data identity forwarding: require the router to encode the original `msg.sender` into `extensionData`, and have the extension decode and verify it. The extension should reject calls where the decoded identity does not match an allowlisted address. Alternatively, if router-mediated swaps are intentionally excluded from allowlisted pools, the extension should revert when `sender` is a known router address, and documentation must clearly state this restriction.

## Proof of Concept
```
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           zeroForOne: true,
           amountIn: X,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router
6. Pool calls _beforeSwap(router, bob, ...)
7. ExtensionCalling encodes sender=router and calls extension.beforeSwap(router, ...)
8. Extension evaluates: allowedSwapper[pool][router] == true  → passes
9. Bob's swap executes on the curated pool despite never being allowlisted.
```

The corrupted value is `allowedSwapper[pool][router]`, which is used as a proxy for the actual user identity check. The extension's per-user gate is reduced to a per-intermediary gate, making the allowlist ineffective against any caller of the public router.