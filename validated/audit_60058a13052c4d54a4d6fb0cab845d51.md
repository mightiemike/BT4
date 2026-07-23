Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user — allowlist fully bypassed for router-mediated swaps - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` (the router contract) as the `sender` argument to `_beforeSwap`, so `SwapAllowlistExtension.beforeSwap` checks whether the **router** is allowlisted, not the original EOA. Any pool admin who allowlists the router to permit router-mediated swaps for legitimate users simultaneously grants every unprivileged user the ability to bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description

**Call path:**

1. Unprivileged user (Charlie, not on the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle()`.
2. The router calls `IMetricOmmPoolActions(params.pool).swap(recipient, ...)` — at this point `msg.sender` seen by the pool is the **router address**, not Charlie.
3. `MetricOmmPool.swap()` invokes `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router.
   - `metric-core/contracts/MetricOmmPool.sol` line 231: `_beforeSwap(msg.sender, recipient, ...)`
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`.
5. Inside `SwapAllowlistExtension.beforeSwap`:
   ```solidity
   if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
       revert NotAllowedToSwap();
   }
   ```
   `msg.sender` is the pool; `sender` is the **router**. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][Charlie]`.

**Why existing guards fail:**

The `onlyPool` modifier in `BaseMetricExtension` only verifies that the caller is a factory-registered pool — it does not recover the original EOA. There is no transient-storage or callback mechanism that threads the original `msg.sender` through the pool into the hook. The pool unconditionally forwards its own `msg.sender` (the router) as `sender`.

**Bypass condition:** The pool admin must allowlist the router address (`allowedSwapper[pool][router] = true`). This is the only way to allow any user to swap through the router on an allowlisted pool. Once done, every user — including those the allowlist was meant to exclude — can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the hook will pass because it sees the allowlisted router address, not the individual caller.

## Impact Explanation
The `SwapAllowlistExtension` is designed to gate swap access to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market participants). When the router is allowlisted (the only way to enable router-mediated swaps), the gate is completely open to all callers. Any non-allowlisted user can execute swaps against a pool that was intended to be restricted, receiving output tokens they should not be entitled to receive. This constitutes broken core pool functionality and an admin-boundary break: the pool admin's intended access control is silently nullified by the router indirection.

## Likelihood Explanation
The bypass is trivially reachable by any unprivileged EOA with no special setup beyond calling the public router. It is repeatable on every block. The only precondition — the router being allowlisted — is a necessary operational step for any pool that wants to support router-mediated swaps for its legitimate users, making the vulnerable configuration highly likely in production.

## Recommendation
Thread the original caller identity through the pool into the hook. One approach: add an `originator` field to the pool's `swap` signature (or store it in transient storage before the hook call) so that `_beforeSwap` can pass the true EOA rather than `msg.sender`. Alternatively, `SwapAllowlistExtension.beforeSwap` should check `recipient` or a separately attested caller field rather than the raw `sender` argument, which is the intermediary contract when a router is used. The router itself could attest the original `msg.sender` via `extensionData`, but that requires the hook to verify the router's signature to prevent spoofing.

## Proof of Concept

```solidity
// Foundry test sketch
function test_allowlistBypass() public {
    // Pool admin allowlists only the router (required for router swaps to work)
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);

    // Charlie is NOT on the allowlist
    address charlie = makeAddr("charlie");
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), charlie));

    // Charlie calls the router — hook sees sender=router (allowlisted), not charlie
    deal(token0, charlie, 1e18);
    vm.startPrank(charlie);
    IERC20(token0).approve(address(router), 1e18);
    // This should revert but does NOT — charlie bypasses the allowlist
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: charlie,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    vm.stopPrank();
}
```