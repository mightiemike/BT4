Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which resolves to `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router address. If the pool admin allowlists the router to support router-mediated swaps, any unprivileged user can bypass the per-user allowlist entirely by calling through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the argument passed by the pool:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmPool.swap()` passes its own `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
    msg.sender,  // <-- whoever called pool.swap()
    ...
```

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` of the pool's `swap()` call:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who allowlists the router (the only way to support router-mediated swaps for allowlisted users) inadvertently grants every user who calls through the router a passing check. The original user identity is stored in transient storage via `_setNextCallbackContext` for payment purposes only and is never forwarded to the extension. The `extensionData` passed to `pool.swap()` is hardcoded as `""` by the router for `exactInputSingle`, and even if it were not, the extension does not read `extensionData` to recover the original caller.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner explicitly passed to `addLiquidity`), which is preserved correctly through the liquidity adder path.

## Impact Explanation
Any unprivileged user can swap on a pool restricted by `SwapAllowlistExtension` by routing through the public `MetricOmmSimpleRouter`. The swap allowlist — the primary access-control mechanism for curated pools — is rendered completely ineffective for router-mediated swaps. This is a direct bypass of the pool's curation guarantee, allowing unauthorized parties to extract liquidity from pools designed to serve only specific counterparties (e.g., KYC'd users, institutional partners). This constitutes broken core pool functionality and a direct admin-boundary break reachable by any unprivileged caller.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router. This is the natural and expected configuration: a pool that wants to support router-mediated swaps for its allowlisted users must allowlist the router — there is no other way to enable router support while keeping the allowlist active. The exploit is therefore reachable in any production deployment of a curated pool that supports the standard periphery router, with no special attacker capability required beyond calling a public function.

## Recommendation
The extension should not rely on `sender` (the intermediary caller) but on the economically relevant actor. Two approaches:

1. **Forward original caller via `extensionData`**: Modify `MetricOmmSimpleRouter` to encode `msg.sender` into `extensionData` before calling `pool.swap()`, and update `SwapAllowlistExtension.beforeSwap` to decode and check it when present.
2. **Document incompatibility and enforce at factory level**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and provide a dedicated router variant that passes the original `msg.sender` through `extensionData` so the extension can check it.

## Proof of Concept
**Setup:**
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
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
- Extension checks `allowedSwapper[pool][router]` → `true`
- Bob's swap succeeds despite not being individually allowlisted