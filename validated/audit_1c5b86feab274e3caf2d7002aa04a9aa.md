Audit Report

## Title
SwapAllowlistExtension Checks Router Address as `sender` Instead of Actual User — Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
When a user swaps through `MetricOmmSimpleRouter`, the pool's `swap` function receives the router contract as `msg.sender` and passes it as `sender` to `ExtensionCalling._beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. The extension then checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actualUser]`. If a pool admin allowlists the router address to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist. If the router is not allowlisted, individually allowlisted users cannot use the router at all, breaking core swap functionality.

## Finding Description

**Call path:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput` / `exactOutputSingle` / `exactOutput`).
2. The router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`. At this point `msg.sender` inside the pool is the **router address**, not the original user.
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passing the router address as `sender`.
4. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to every configured extension via `_callExtensionsInOrder`.
5. `SwapAllowlistExtension.beforeSwap(address sender, ...)` executes:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the **router** (wrong). The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Root cause:** The pool's `swap` function has no explicit `sender` parameter; it always uses `msg.sender` (the immediate caller). When the router intermediates, the immediate caller is the router, so the extension never sees the real user.

**Why existing guards fail:** The `_requireExpectedCallbackCaller` check in the router only validates that the callback comes from the expected pool; it does not restore or forward the original user identity to the pool. No mechanism in `MetricOmmPool.swap`, `ExtensionCalling._beforeSwap`, or `SwapAllowlistExtension.beforeSwap` recovers the original `msg.sender` of the router call.

**Exploit flow (bypass):**
- Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router address (`setAllowedToSwap(pool, router, true)`) so that allowlisted users can reach the pool via the router.
- Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
- The extension sees `sender = router`, which is allowlisted → swap succeeds for every user.

**Exploit flow (broken functionality):**
- Pool admin allowlists specific EOAs/contracts but not the router.
- An allowlisted user calls the router → extension sees `sender = router` → `NotAllowedToSwap` revert → allowlisted user cannot use the supported periphery path.

## Impact Explanation
A curated pool's per-user swap allowlist is either fully bypassed (if the router is allowlisted) or rendered incompatible with the supported router path (if it is not). In the bypass case, any unprivileged trader can execute swaps on a pool that was designed to restrict trading to a specific set of addresses, directly violating the allowlist invariant and enabling unauthorized fund flows through the pool. This constitutes a broken core pool functionality / admin-boundary break with direct loss potential on curated pools. Severity: **High**.

## Likelihood Explanation
The router is the primary supported swap entry point for end users. Any pool that deploys `SwapAllowlistExtension` and expects users to interact via the router will encounter this issue on every router-mediated swap. No special attacker capability is required beyond calling the public router functions. The condition is trivially reproducible and repeatable.

## Recommendation
The pool's `swap` function should accept an explicit `sender` parameter (the intended swapper identity) separate from `msg.sender` (the immediate caller), and pass that value to `_beforeSwap`. The router should forward `msg.sender` (the real user) as this explicit sender. Alternatively, `SwapAllowlistExtension.beforeSwap` should not rely on the `sender` argument passed by the pool when the immediate caller is a known router; however, the cleanest fix is at the pool/router interface level so all extensions benefit uniformly.

## Proof of Concept

```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Pool configured with SwapAllowlistExtension; router is allowlisted
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // Deposit liquidity (depositor is allowlisted separately)
    depositExtension.setAllowedToDeposit(address(pool), address(this), true);
    _addLiquidity(...);

    // Unprivileged user — NOT individually allowlisted
    address attacker = makeAddr("attacker");
    deal(address(token0), attacker, 1e18);
    vm.startPrank(attacker);
    token0.approve(address(router), type(uint256).max);

    // Should revert but succeeds because sender == router (allowlisted)
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: attacker,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker received token1 despite not being on the allowlist
}
```