Audit Report

## Title
Swap Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the hook checks whether the **router** is allowlisted rather than the end user. Any unprivileged user can bypass the allowlist entirely by routing through the router once the router has been allowlisted — a necessary operational step for any allowlisted user to use the router.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- direct caller of pool.swap(), not the originating user
  recipient, zeroForOne, amountSpecified, priceLimitX64,
  packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the forwarded value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router, and the original end-user address is never passed to the extension:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

The router stores the original `msg.sender` only in transient callback context (for payment purposes), never in `extensionData`. The hook therefore receives `sender = router_address` and evaluates `allowedSwapper[pool][router]`. The actual end user's address is never checked. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses must also allowlist the router for those addresses to use it. Once the router is allowlisted, **any** address — including those explicitly excluded — can bypass the gate by calling any `exact*` function on `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps, breaking the core access-control invariant the extension is designed to enforce. This is a broken core pool functionality causing the allowlist restriction to be completely ineffective, matching the "Allowlist path" smart audit pivot: allowlist checks must cover the exact actor/action intended and cannot be bypassed through the router.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router address. This is not a contrived precondition — it is a necessary operational step. Without allowlisting the router, even legitimately allowlisted users cannot use the router (the hook would check `allowedSwapper[pool][router]` which would be `false`). Any pool that uses `SwapAllowlistExtension` with a non-open allowlist and also wants to support router-based swaps is vulnerable. The bypass is repeatable by any unprivileged address at any time.

## Recommendation
The `sender` passed to extension hooks must represent the originating user, not the immediate caller. Two complementary fixes:

1. **Router-side**: Have the router encode the true `msg.sender` (the end user) inside `extensionData` and document that extensions should read it from there when the caller is a known trusted router.
2. **Extension-side**: `SwapAllowlistExtension` should detect whether `sender` is a known trusted router and, if so, decode the real user from `extensionData` before performing the allowlist lookup.

A cleaner long-term fix is for the pool to accept an explicit `originator` parameter distinct from `msg.sender`, so extensions always receive the true initiating address regardless of routing depth.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured and `allowAllSwappers[pool] = false`.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`, checks `allowedSwapper[pool][router] == true`, and **passes**.
7. Bob's swap executes successfully despite not being on the allowlist.