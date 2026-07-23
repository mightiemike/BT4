Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the end user. If the pool admin allowlists the router (a necessary step for any allowlisted user to use the router), every unprivileged user can bypass the allowlist by routing through the same router, defeating the entire curation policy.

## Finding Description

**Root cause — wrong actor binding in `beforeSwap`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the value forwarded by `ExtensionCalling._beforeSwap`, which encodes `msg.sender` of the pool's own `swap()` call:

```solidity
// metric-core/contracts/MetricOmmPool.sol L231
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient, ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
```

So `msg.sender` inside `pool.swap()` = the router contract. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Exploit flow:**

1. Pool is deployed with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Alice wants to use the router. Admin allowlists the router: `setAllowedToSwap(pool, router, true)`.
4. Charlie (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(...)` → `msg.sender` = router → extension checks `allowedSwapper[pool][router]` = `true` → swap succeeds.
6. Charlie has bypassed the allowlist and executed a swap on a curated pool.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` on the router, and to any intermediate hop in a multi-hop path.

**Why existing guards fail:**

The `onlyPool` modifier on the extension correctly restricts who can call `beforeSwap` to the pool itself. The pool correctly passes `msg.sender` as `sender`. Neither guard distinguishes between the router and the actual end user. The `isAllowedToSwap` view function checks `allowedSwapper[pool][swapper]` keyed by the user address, giving the admin a false sense that individual users are gated, while the actual on-chain check is keyed by the router address.

## Impact Explanation

A non-allowlisted user can execute swaps on a curated pool that is supposed to restrict trading to approved counterparties. This directly breaks the pool's curation policy and allows unauthorized parties to trade against LP positions, potentially extracting value from LPs who deposited under the assumption that only vetted counterparties would trade. This is a broken core pool functionality / admin-boundary break with direct fund impact on LP assets.

## Likelihood Explanation

The precondition is that the pool admin allowlists the router. This is a realistic and expected operational step: any allowlisted user who wants to use the supported periphery router requires the router to be allowlisted. The admin has no indication from the interface or the `isAllowedToSwap` view that allowlisting the router opens the pool to all users. Once the router is allowlisted (a one-time admin action), the bypass is trivially repeatable by any unprivileged address with zero additional preconditions.

## Recommendation

The extension must gate the actual end user, not the immediate caller of `pool.swap()`. Two options:

1. **Pass the original `msg.sender` through the router.** The router stores the original caller in transient storage (already done for the payer context). Extend the `extensionData` or a dedicated transient slot to carry the original caller, and have the extension read it. This requires a coordinated change between the router and the extension.

2. **Check `sender` only for direct pool calls; reject router-mediated calls unless the router is explicitly allowlisted with a separate flag.** Add a `isAllowedRouter` mapping and require that router-mediated swaps carry a signed proof of the end user's allowlist status.

The simplest safe fix is to document that allowlisting the router is equivalent to `setAllowAllSwappers(pool, true)` and remove the ability to allowlist the router address individually, forcing admins to use `allowAllSwappers` if they want open router access.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: pool with SwapAllowlistExtension, alice allowlisted, router allowlisted
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Charlie is NOT allowlisted
    assertFalse(swapExtension.isAllowedToSwap(address(pool), charlie));

    // Charlie bypasses via router
    vm.prank(charlie);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        recipient: charlie,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```