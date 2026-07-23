Audit Report

## Title
`SwapAllowlistExtension::beforeSwap` checks router address instead of end-user, making per-pool swap allowlist bypassable via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, producing two mutually exclusive failure modes: allowlisted users are locked out of the router, or — if the admin allowlists the router to restore access — any non-allowlisted user can bypass the guard entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `_callExtensionsInOrder`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The lookup is `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` with no mechanism to forward the originating user's address:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient, params.zeroForOne,
    MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
    priceLimitX64, "", params.extensionData
  );
```

`params.extensionData` is forwarded but `SwapAllowlistExtension.beforeSwap` ignores the `bytes calldata` parameter entirely (it is unnamed and unused), so there is no existing path to convey user identity through the router.

**Failure Mode A**: Admin allowlists `userA`. Direct `pool.swap()` by `userA` succeeds (`allowedSwapper[pool][userA] = true`). Router call by `userA` reverts (`allowedSwapper[pool][router] = false` → `NotAllowedToSwap`). The router is permanently unusable for any allowlisted user.

**Failure Mode B**: Admin allowlists the router to restore router access (`allowedSwapper[pool][router] = true`). Now any address — including those the admin never allowlisted — can call `router.exactInputSingle(...)` and the check passes unconditionally, because the resolution is `allowedSwapper[pool][router]` regardless of who the actual caller is. The allowlist is fully neutralised.

The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`, all of which call `pool.swap()` directly.

## Impact Explanation
`SwapAllowlistExtension` is the protocol's only per-pool swap access-control primitive. In Failure Mode A, allowlisted users are forced to interact directly with the pool contract, bypassing slippage protection (`amountOutMinimum`), multi-hop routing, and deadline enforcement provided by the router — a broken core swap flow. In Failure Mode B, the guard is fully neutralised: any address can swap in a pool the admin intended to restrict. If the allowlist was deployed to exclude adversarial or non-KYC'd traders, those traders can execute informed swaps against LP positions, directly impacting LP principal. This meets the "broken core pool functionality" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
Failure Mode A is triggered by the default correct configuration (allowlist individual users, not the router). It affects every pool that deploys `SwapAllowlistExtension` and expects users to use the router — a standard and expected usage pattern. Failure Mode B is triggered when the pool admin takes the natural remediation step of allowlisting the router to restore router access. No special attacker capability is required beyond calling the public router functions. Both modes are repeatable and deterministic.

## Recommendation
`SwapAllowlistExtension` must gate the actual end-user, not the intermediary. Two viable approaches:

1. **Signed identity in `extensionData`**: Have the router embed `msg.sender` (the actual user) in `extensionData` and have `SwapAllowlistExtension` decode and verify it, optionally gated by a trusted-router registry.
2. **Router-aware forwarding**: Add a `swapWithSender(address actualSender, ...)` entry point on the pool (callable only by whitelisted routers) that passes `actualSender` instead of `msg.sender` to extension hooks.
3. **Separate router allowlist**: Distinguish between "router is allowed to relay" and "user is allowed to trade" by checking both `allowedSwapper[pool][router]` (relay permission) and a user identity extracted from `extensionData`.

## Proof of Concept
```solidity
// 1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
// 2. Admin allowlists userA only.
swapExt.setAllowedToSwap(address(pool), userA, true);

// 3. userA swaps directly — succeeds (sender = userA, allowedSwapper[pool][userA] = true).
vm.prank(userA);
pool.swap(userA, false, 1000, type(uint128).max, "", "");

// 4. userA swaps via router — REVERTS (sender = router, allowedSwapper[pool][router] = false).
vm.prank(userA);
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userA, ...}));
// → NotAllowedToSwap

// 5. Admin allowlists the router to fix userA's access.
swapExt.setAllowedToSwap(address(pool), address(router), true);

// 6. Non-allowlisted userB now bypasses the guard via router — SUCCEEDS.
vm.prank(userB);  // userB was never allowlisted
router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: userB, ...}));
// → swap executes; allowlist is fully bypassed
```