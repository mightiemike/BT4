Audit Report

## Title
`SwapAllowlistExtension` gates on the router address instead of the real swapper, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted — the natural configuration for a public periphery router — every user, including those explicitly excluded, can bypass the allowlist by calling through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← always the direct caller of pool.swap
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L149-177
function _beforeSwap(address sender, ...) internal {
  _callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
  );
}
```

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap` directly, making the router the pool's `msg.sender`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The full broken call chain:
```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient=User, ...)   // msg.sender = router
              → _beforeSwap(sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router)
                        → checks allowedSwapper[pool][router] ← wrong identity
```

The extension never sees the real user address. The allowlist mapping is keyed on `(pool, sender)` where `sender` is the router, not the economic actor.

## Impact Explanation
`SwapAllowlistExtension` is the sole on-chain mechanism for restricting who may swap against a pool. If the pool admin allowlists the router (the expected configuration so that legitimate users can use the public periphery), the allowlist is completely neutralised: any address — including those the admin explicitly excluded — can call `MetricOmmSimpleRouter` and swap freely. The pool's access-control invariant is broken for every router-mediated swap, which is the primary user-facing entry point. This constitutes broken core pool functionality causing loss of the intended access restriction, meeting the "Broken core pool functionality" allowed impact criterion at High severity.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard public swap entry point. Any pool that deploys `SwapAllowlistExtension` and also allowlists the router (or any other intermediary contract) is immediately vulnerable. No special privileges or malicious setup are required — a normal user simply calls the router instead of the pool directly. The precondition (router allowlisted) is the expected operational state for any pool using the public periphery.

## Recommendation
The extension must gate on the economic actor (the end user), not the intermediary. Two complementary fixes:

1. **Pass the real user through the router.** Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that identity when `sender` is a known router.

2. **Preferred — forwarded `realSender` field.** Modify the `beforeSwap` signature or the router to carry the originating user address explicitly, and update `SwapAllowlistExtension` to check `allowedSwapper[pool][realSender]` instead of `allowedSwapper[pool][sender]`.

The simplest safe fix is to have the router encode `msg.sender` into `extensionData` and have the extension decode and verify it, so the allowlist always gates the address that controls the funds.

## Proof of Concept
**Setup:**
- Pool deployed with `SwapAllowlistExtension`.
- Pool admin allowlists `MetricOmmSimpleRouter`: `setAllowedToSwap(pool, router, true)`.
- Alice (`0xAlice`) is **not** on the allowlist.

**Attack:**
1. Alice calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: Alice, ...})`.
2. Router calls `pool.swap(recipient=Alice, ...)` — pool's `msg.sender` = router.
3. Pool calls `_beforeSwap(sender=router, ...)`.
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`.
5. Swap proceeds. Alice swaps successfully despite being excluded from the allowlist.

**Foundry test sketch:**
```solidity
function test_allowlistBypass() public {
    // admin allowlists router, not alice
    ext.setAllowedToSwap(address(pool), address(router), true);
    assertFalse(ext.isAllowedToSwap(address(pool), alice));

    // alice routes through the router — should revert but does not
    vm.prank(alice);
    router.exactInputSingle(ExactInputSingleParams({pool: address(pool), recipient: alice, ...}));
    // swap succeeds: allowlist bypassed
}
```