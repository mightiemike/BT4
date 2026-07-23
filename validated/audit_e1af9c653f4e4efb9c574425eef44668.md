Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Original User — Allowlist Bypass via Router (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is the pool's `msg.sender` — the router contract — not the original end-user. When a pool admin allowlists the router to permit router-mediated swaps, every unprivileged user can bypass the curated allowlist by routing through `MetricOmmSimpleRouter`. The allowlist gate is rendered meaningless for all router-path swaps.

## Finding Description

**Root cause — wrong actor bound to `sender`:**

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
  msg.sender,   // ← pool's caller; the ROUTER when entered via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol  line 160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...))   // sender = router address
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Exploit flow:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific KYC'd addresses (e.g. `alice`, `bob`).
2. To let those users trade through the standard router, the admin must also call `setAllowedToSwap(pool, router, true)` — otherwise every router call reverts.
3. Once the router is allowlisted, any unprivileged user `charlie` calls `MetricOmmSimpleRouter.exactInputSingle(...)`. The router calls `pool.swap(...)`, the pool passes `msg.sender = router` to `beforeSwap`, the extension sees `allowedSwapper[pool][router] == true`, and the check passes — `charlie` trades freely on the curated pool.

**Existing guards are insufficient:**

The `onlyPool` modifier in `BaseMetricExtension` only verifies that the caller of `beforeSwap` is a known pool; it does not verify the identity of the original user. There is no mechanism in the extension or the pool to thread the original `tx.origin` or a user-supplied identity through the call stack in a tamper-proof way.

## Impact Explanation
A curated pool whose entire purpose is to restrict trading to an approved set of addresses is completely open to any unprivileged user via the router. This constitutes a broken core pool functionality / admin-boundary break: the pool admin's allowlist policy is bypassed, allowing unauthorized parties to execute swaps and extract value from a pool that was designed to be restricted. Severity: **High**.

## Likelihood Explanation
The condition is reachable by any unprivileged user with no special privileges. The only precondition is that the pool admin has allowlisted the router (a natural and expected action for any pool that intends to support the standard periphery). The attack is repeatable every block and requires no capital beyond the swap amount.

## Recommendation
Pass the original user identity through the call stack. The simplest fix is to have the pool accept an explicit `swapper` parameter in `swap` (or derive it from transient storage set by the router before calling the pool), and pass that value — rather than `msg.sender` — as `sender` to `_beforeSwap`. Alternatively, `SwapAllowlistExtension.beforeSwap` could read `tx.origin`, but that is fragile. The cleanest solution mirrors how the router already stores the payer in transient storage: store the original `msg.sender` in transient storage before the pool call and expose it via a callback so the extension can read the true initiator.

## Proof of Concept

```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // 1. Deploy pool with SwapAllowlistExtension
    // 2. Admin allowlists `alice` and the router
    extension.setAllowedToSwap(pool, alice, true);
    extension.setAllowedToSwap(pool, address(router), true);

    // 3. `charlie` (not allowlisted) swaps through the router
    vm.prank(charlie);
    router.exactInputSingle(ExactInputSingleParams({
        pool: pool,
        recipient: charlie,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
```