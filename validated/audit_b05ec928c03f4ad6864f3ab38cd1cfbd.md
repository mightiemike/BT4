Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any user to bypass per-pool swap restrictions via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router to support router-mediated swaps, every user — including those explicitly excluded from the individual allowlist — can bypass the restriction by calling the router instead of the pool directly. The allowlist cannot simultaneously enforce per-user restrictions and support the official periphery router.

## Finding Description
**Root cause — `MetricOmmPool.swap` passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231. When the call originates from `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user.

**Root cause — `ExtensionCalling._beforeSwap` forwards `sender` unchanged:**

`ExtensionCalling._beforeSwap` (lines 149–177) passes `sender` directly into the `beforeSwap` extension call without modification.

**Root cause — `SwapAllowlistExtension.beforeSwap` checks the wrong actor:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router, not the user.

**Exploit flow:**

1. Pool admin deploys a pool with `SwapAllowlistExtension` and allowlists only specific users (e.g., Alice).
2. Pool admin also calls `setAllowedToSwap(pool, routerAddress, true)` to enable router-mediated swaps for allowlisted users.
3. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
4. The router calls `pool.swap(recipient, ...)` — `msg.sender` inside the pool is the router.
5. `_beforeSwap` passes the router address as `sender` to the extension.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Bob successfully swaps on a pool he was explicitly excluded from.

**Why existing guards fail:**

There is no mechanism in the router or pool to thread the original `msg.sender` (the end user) through to the extension. The `extensionData` bytes are user-controlled and not authenticated, so they cannot be trusted to carry the real caller identity. The `SwapAllowlistExtension` has no awareness of the router and no fallback to check the economic actor.

## Impact Explanation
The `SwapAllowlistExtension` is the production access-control guard for curated pools. Its bypass means any unprivileged user can execute swaps on pools explicitly configured to restrict trading to a specific set of addresses. This breaks the core pool functionality of access-controlled pools and constitutes a broken invariant: "A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it." The impact is unauthorized swap execution on restricted pools, which can cause direct loss of funds to LPs in pools designed for controlled trading environments (e.g., institutional or KYC-gated pools).

## Likelihood Explanation
The precondition — the router being allowlisted — is a natural and expected operational step for any pool admin who wants to support both access control and router-mediated swaps. The `MetricOmmSimpleRouter` is the official periphery router. Any unprivileged user can execute this bypass with a single public call to the router. No special privileges, flash loans, or multi-step setup are required beyond the router being allowlisted. The bypass is repeatable on every swap.

## Recommendation
The fix must ensure the extension checks the originating user, not the intermediate caller. Options:

1. **Pass the original caller through the router via authenticated `extensionData`:** The router encodes `msg.sender` into `extensionData` and the extension decodes and verifies it when `sender` is a known router. This requires a trusted router registry.
2. **Check `recipient` instead of `sender`:** If the pool's design intent is to gate who receives swap output, `recipient` is user-controlled and not router-substituted. However, this changes the semantic of the allowlist.
3. **Preferred — add a router-aware sender resolution in the extension:** The extension checks if `sender` is a registered router; if so, it reads the actual user from authenticated transient storage set by the router before calling `pool.swap`.

## Proof of Concept
```solidity
// Foundry test sketch
function test_routerBypassesSwapAllowlist() public {
    // Setup: pool with SwapAllowlistExtension, only Alice allowlisted
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    // Admin also allowlists router to support router-mediated swaps
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Bob is NOT allowlisted
    // Bob calls router directly
    vm.startPrank(bob);
    token0.approve(address(router), type(uint256).max);
    // This should revert but does NOT — bob bypasses the allowlist
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    vm.stopPrank();
    // Bob successfully swapped despite not being on the allowlist
}
```