Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address. If the pool admin allowlists the router to support router-mediated swaps, any unprivileged user can bypass the per-user allowlist entirely by calling through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument forwarded from the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of the pool call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The router stores the original `msg.sender` only in transient callback context for payment purposes; it is never forwarded to the pool or extension as the identity of the swapper. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

A pool admin who wants to support router-mediated swaps for allowlisted users must allowlist the router. Once the router is allowlisted, the check passes for every user who routes through it, regardless of individual allowlist status. The `extensionData` field is available in `beforeSwap` but the extension ignores it entirely, so there is no existing mechanism to recover the original caller identity.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `allowedDepositor[msg.sender][owner]` where `owner` is the explicit position-owner argument to `addLiquidity`, not the intermediary caller — so it does not share this flaw.

## Impact Explanation
The swap allowlist is the primary access-control mechanism for curated pools (e.g., KYC-gated, institutional-only). Any unprivileged user can execute swaps on such a pool by routing through the public `MetricOmmSimpleRouter`, completely defeating the allowlist. This constitutes broken core pool functionality causing direct unauthorized access to restricted liquidity, meeting the "broken core pool functionality" and "admin-boundary break" impact criteria.

## Likelihood Explanation
The precondition — the router being allowlisted — is the natural and expected configuration for any curated pool that also wants to support router-mediated swaps for its allowlisted users. There is no other way to enable router support while keeping the allowlist active. The exploit is therefore reachable in any production deployment of a curated pool that supports the standard periphery router, requires no special privileges, and is repeatable by any address.

## Recommendation
The extension must check the economically relevant actor, not the intermediary. Two viable approaches:

1. **Pass original caller via `extensionData`**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before calling `pool.swap()`, and update `SwapAllowlistExtension.beforeSwap` to decode and check it when present.

2. **Document incompatibility and enforce at factory level**: Prohibit allowlisting the router on `SwapAllowlistExtension`-gated pools, or provide a dedicated allowlist-aware router that forwards the original caller identity through `extensionData` so the extension can verify it.

The simplest short-term fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes and checks it instead of (or in addition to) the raw `sender` argument.

## Proof of Concept

**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowlisted user.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted to support router-mediated swaps for Alice.

**Exploit (Bob, not allowlisted):**
```solidity
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(curated_pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
```

**Trace:**
- `router.exactInputSingle()` → `pool.swap(recipient=bob, ...)` with `msg.sender = router`
- Pool calls `_beforeSwap(sender=router, ...)`
- Extension evaluates `allowedSwapper[pool][router]` → `true`
- Bob's swap succeeds despite not being individually allowlisted

**Foundry test plan:** Deploy pool + extension, configure allowlist as above, call `router.exactInputSingle` from an address not in the allowlist, assert the swap succeeds (no `NotAllowedToSwap` revert).