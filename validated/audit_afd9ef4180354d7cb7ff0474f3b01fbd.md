Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract address, not the actual user. This means the allowlist cannot distinguish between individual users going through the router: if the router address is allowlisted (which is required for any allowed user to use the router), every unprivileged user can bypass the per-user swap allowlist by routing through the router.

## Finding Description

**Root cause — `sender` binding in the pool:**

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap()` forwards this value verbatim to every configured extension:

```solidity
// ExtensionCalling.sol:160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

**Root cause — allowlist check in the extension:**

`SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the value forwarded from the pool:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Root cause — router replaces the user as `msg.sender`:**

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
);
```

So when a user calls `router.exactInputSingle()`, the call chain is:

```
user → router.exactInputSingle()
         → pool.swap()   [msg.sender = router]
             → _beforeSwap(sender = router, ...)
                 → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                     → checks allowedSwapper[pool][router]
```

The extension sees the router address as `sender`, not the actual user. The pool admin faces an impossible choice:
- **Do not allowlist the router** → all allowed users are blocked from using the router
- **Allowlist the router** → every user, including disallowed ones, can bypass the per-user allowlist by routing through the router

**Existing guards are insufficient:** The `onlyPool` modifier in `BaseMetricExtension` only verifies that the caller is a registered pool; it does not recover the original user identity. There is no mechanism in the extension call path to propagate the original EOA through the router.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd traders, whitelisted market makers) can be bypassed by any unprivileged user calling `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` / `exactOutputSingle()` / `exactOutput()`. Once the router is allowlisted (a prerequisite for any allowed user to use the router), the per-user allowlist is nullified for all router-mediated swaps. This is a direct policy bypass on curated pools — the exact impact the allowlist was designed to prevent.

## Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary public swap entrypoint for EOA users. Any unprivileged user can call it permissionlessly. The bypass requires only that the router address is on the allowlist, which is a necessary operational condition for the router to be usable at all. No special privileges, flash loans, or multi-transaction setup are required. The attack is repeatable on every block.

## Recommendation

The extension should receive the original user identity, not the intermediary contract address. Two options:

1. **Pass the original payer/user through the router as an explicit argument** and have the pool forward it to extensions as a separate `originator` field — this requires interface changes.
2. **Check `sender` against the allowlist in the router itself** before calling `pool.swap()`, and document that the extension only gates direct pool callers. The router would need its own allowlist enforcement.
3. **Alternatively**, the `SwapAllowlistExtension` can be redesigned to allowlist the router and rely on a separate router-level allowlist that gates by `msg.sender` of the router call — but this requires a coordinated two-layer enforcement design.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// The router is also allowlisted so that `allowedUser` can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: disallowedUser calls the router directly.
vm.prank(disallowedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: disallowedUser,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// ✓ Swap succeeds — allowlist bypassed because extension sees sender = router (allowlisted),
//   not disallowedUser (not allowlisted).
```