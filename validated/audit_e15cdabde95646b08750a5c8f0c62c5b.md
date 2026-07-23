Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every registered extension. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. `SwapAllowlistExtension.beforeSwap` therefore evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router — the only way to let legitimate users trade through the supported periphery — simultaneously grants every unpermissioned address on the network the ability to bypass the allowlist.

## Finding Description

`MetricOmmPool.swap` calls `_beforeSwap` with `msg.sender` as the first argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every registered extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then enforces its policy against that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly, making the router the pool's `msg.sender`:

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

The complete call chain is: `userB → router.exactInputSingle → pool.swap(msg.sender=router) → _beforeSwap(sender=router) → extension.beforeSwap(sender=router)`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][userB]`. No existing guard in the pool, router, or extension breaks this chain. The `_setNextCallbackContext` call on line 71 stores the real `msg.sender` only for the payment callback, not for extension dispatch.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC'd counterparties, whitelisted market makers, protocol-owned addresses) is fully bypassed the moment the admin allowlists the router. Any unpermissioned address can call `MetricOmmSimpleRouter.exactInputSingle` and trade against the pool's liquidity. This constitutes a direct, complete defeat of the access-control mechanism — an unauthorized actor can extract value from LP positions in a pool they were never authorized to access, violating the pool admin's intended access policy and any regulatory or contractual obligations it encodes. This matches the "broken core pool functionality causing loss of funds or unusable swap flows" and "admin-boundary break bypassed by an unprivileged path" impact categories.

## Likelihood Explanation

The bypass requires only that the pool admin allowlists the router, which is the natural and expected action for any admin who wants legitimate users to access the pool through the supported periphery. The router is a public, permissionless contract with no access controls of its own. Once the router is allowlisted, the bypass is trivially reachable by any address with no special privileges, no capital requirements beyond the swap input, and no setup beyond calling `exactInputSingle`. The condition is not hypothetical — it is the normal operational state of any allowlisted pool that supports periphery access.

## Recommendation

The pool's `swap` function should forward the original initiating user as `sender` to extensions rather than its own `msg.sender`. The cleanest fix is to add an explicit `originator` parameter to `IMetricOmmPoolActions.swap` that the router populates with its own `msg.sender` before calling the pool, and that the pool passes through to `_beforeSwap`/`_afterSwap` instead of `msg.sender`. Alternatively, the router can embed the real user address in `extensionData` under a well-defined ABI convention, and `SwapAllowlistExtension` can decode it — but this requires coordinated convention enforcement across all periphery contracts and is fragile. The explicit `originator` field in the pool interface is the robust solution.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` registered as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, userA, true)` — only `userA` is intended to trade.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary so `userA` can use `MetricOmmSimpleRouter`.
4. Unauthorized `userB` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: userB, ...})`.
5. Router calls `pool.swap(recipient=userB, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender=router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `userB` successfully trades in a pool they were never authorized to access.

Foundry test outline:
```solidity
function test_swapAllowlist_bypass_via_router() public {
    // deploy pool with SwapAllowlistExtension
    // allowlist userA and router
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, userA, true);
    vm.prank(poolAdmin);
    ext.setAllowedToSwap(pool, address(router), true);

    // userB (not allowlisted) swaps via router — should revert but does not
    vm.prank(userB);
    router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: userB, ...}));
    // assert userB received output tokens — bypass confirmed
}
```