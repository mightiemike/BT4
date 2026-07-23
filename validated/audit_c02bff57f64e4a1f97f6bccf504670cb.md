Audit Report

## Title
SwapAllowlistExtension gates on router address instead of actual user, allowing allowlist bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
The pool's `swap` function passes `msg.sender` as `sender` to `_beforeSwap`, which is the direct caller of the pool — the router contract — not the originating user. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` against this router address. If the router is allowlisted, any unprivileged user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter`, completely defeating the access control the extension is designed to enforce.

## Finding Description
**Root cause:** In `MetricOmmPool.sol` at line 230–240, `_beforeSwap` is called with `msg.sender` as the `sender` argument. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap(...)` directly, making `msg.sender` inside the pool equal to the router's address, not the originating user.

**Exact call path:**
1. Unprivileged user (not in allowlist) calls `MetricOmmSimpleRouter.exactInputSingle(params)`.
2. Router calls `IMetricOmmPoolActions(params.pool).swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)` — `msg.sender` in the pool is now the router address.
3. Pool calls `_beforeSwap(msg.sender /*= router*/, recipient, ...)`.
4. `ExtensionCalling._beforeSwap` encodes and dispatches to `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender /*= pool*/][sender /*= router*/]`.
6. If the router is allowlisted for that pool, the check passes regardless of who the originating user is.

**Why existing guards fail:** The `SwapAllowlistExtension` has no mechanism to unwrap the router intermediary. It only sees the `sender` argument forwarded by the pool, which is always `msg.sender` of the pool call — the router. The router stores the real payer in transient storage (`_setNextCallbackContext`) for payment purposes but never forwards the originating user's identity to the pool's `swap` call or to the extension.

## Impact Explanation
Any user can bypass a per-user swap allowlist on a restricted pool by routing through the public `MetricOmmSimpleRouter`. This breaks the core access-control invariant of `SwapAllowlistExtension`: that only explicitly allowlisted addresses may swap. Unauthorized swaps on a restricted pool can drain pool liquidity or execute trades that the pool admin intended to gate. This is a broken core pool functionality / admin-boundary break allowing unauthorized fund-impacting swaps.

## Likelihood Explanation
The router is a public, permissionless contract. Any user can call it. The bypass requires only that the router address is allowlisted for the target pool — a natural configuration for any pool that wants to support router-mediated trading while still restricting direct swappers. No special privileges, flash loans, or complex setup are needed. The attack is repeatable on every swap.

## Recommendation
Pass the originating user identity through the swap path. One approach: add an `originator` parameter to `pool.swap` (or encode it in `extensionData`) so the pool can forward the real user to `_beforeSwap`. Alternatively, `SwapAllowlistExtension.beforeSwap` should check both `sender` (the direct caller/router) and a separately forwarded originator address. The simplest fix consistent with the current architecture is to have the router encode `msg.sender` (the originating user) into `extensionData` and have `SwapAllowlistExtension` decode and gate on that value when present.

## Proof of Concept
```
1. Deploy a pool with SwapAllowlistExtension configured (allowAllSwappers = false).
2. Allowlist only the MetricOmmSimpleRouter address via setAllowedToSwap(pool, router, true).
3. Have an unprivileged EOA (not in allowlist) call MetricOmmSimpleRouter.exactInputSingle with that pool.
4. Observe: the swap succeeds because SwapAllowlistExtension sees sender = router (allowlisted),
   not the originating EOA (not allowlisted).
5. Confirm: a direct pool.swap() call from the same EOA reverts with NotAllowedToSwap.
```

Foundry test sketch:
```solidity
function test_swapAllowlistBypass() public {
    // pool has SwapAllowlistExtension; only router is allowlisted
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // attacker is NOT allowlisted
    vm.startPrank(attacker);
    // direct swap reverts
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(attacker, true, 1000, 0, "", "");

    // router-mediated swap succeeds — bypass confirmed
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool), recipient: attacker, tokenIn: token0,
        zeroForOne: true, amountIn: 1000, amountOutMinimum: 0,
        priceLimitX64: 0, deadline: block.timestamp, extensionData: ""
    }));
    vm.stopPrank();
}
```