Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the immediate caller of the pool, so `sender = router`. If the router is allowlisted (the only way to enable router-mediated swaps for legitimate users), every non-allowlisted user can bypass the per-user allowlist by routing through the router.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // router address when called via MetricOmmSimpleRouter
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged into the extension call. `SwapAllowlistExtension.beforeSwap` then checks that forwarded address against the per-pool allowlist:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong — should be the end user). When Bob calls `router.exactInputSingle({recipient: bob, ...})`, the router calls `pool.swap(recipient=bob, ...)`, so the pool sees `msg.sender = router` and passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][bob]`. If the router is allowlisted (which is required for any legitimate user to use the router), Bob's swap passes the guard unconditionally.

The `onlyPool` modifier in `BaseMetricExtension` only verifies the extension is called by a registered pool — it does not verify the identity of the original swap initiator.

## Impact Explanation
Any unprivileged user can trade on a curated, allowlist-restricted pool by routing through `MetricOmmSimpleRouter`, provided the pool admin has allowlisted the router. This is a direct bypass of the core access-control invariant: a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it. LP depositors on a restricted pool accepted liquidity risk only for a curated counterparty set; this bypass exposes them to unrestricted counterparty flow. This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation
Pool admins who deploy a `SwapAllowlistExtension` and also want their allowlisted users to use the router will naturally add the router to the allowlist — this is the expected operational path. The bypass requires no privileged access from the attacker, only knowledge that the router is allowlisted. It is repeatable on every swap through the router.

## Recommendation
The extension must check the actual end user, not the immediate pool caller. Two viable approaches:

1. **Gate on `recipient`**: For swap allowlists, check the second parameter (`recipient`) rather than `sender`, since `recipient` is always the economic beneficiary and is set to the end user even when routing through the periphery. This is consistent with how `DepositAllowlistExtension` gates on `owner` (the economic LP) rather than `sender` (the adder contract).

2. **Router-forwarded identity**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the immediate `sender` is a known router.

Option 1 is simpler and requires no router changes.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  Alice (allowlisted): allowedSwapper[pool][alice] = true
  Router (allowlisted to enable Alice's router swaps): allowedSwapper[pool][router] = true

Attack:
  Bob (not allowlisted) calls:
    router.exactInputSingle({ tokenIn, tokenOut, recipient: bob, ... })

  Router calls:
    pool.swap(recipient=bob, ...)          // msg.sender = router

  Pool calls:
    _beforeSwap(sender=router, ...)

  Extension evaluates:
    allowedSwapper[pool][router] == true   // passes

  Result: Bob's swap executes on the curated pool.
  Expected: revert NotAllowedToSwap.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only Alice and the router, call `router.exactInputSingle` as Bob with `recipient=bob`, assert the swap succeeds (demonstrating the bypass) rather than reverting with `NotAllowedToSwap`.