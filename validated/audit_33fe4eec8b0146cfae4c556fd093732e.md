Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which equals `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. If the pool admin allowlists the router to enable router-mediated swaps for allowlisted users, every unprivileged user can bypass the per-user allowlist by routing through the router.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the address the pool received as its own `msg.sender` during `swap`.

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:
```solidity
_beforeSwap(msg.sender, recipient, zeroForOne, ...);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)`, making the router `msg.sender` of the pool's `swap` call. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

This produces two broken states:
1. **Router not allowlisted** — allowlisted users cannot use the supported periphery at all; their router-mediated swaps revert with `NotAllowedToSwap`.
2. **Router allowlisted** — every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router address and approves it.

`DepositAllowlistExtension` does not share this flaw — it correctly checks the `owner` parameter (the position owner), not `sender`, confirming the inconsistency is specific to `SwapAllowlistExtension`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for curated access (KYC compliance, exclusive LP pools, institutional-only venues) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. If the admin allowlists the router — a natural step to let allowlisted users benefit from routing — the allowlist is nullified for all router-mediated swaps. Unauthorized users trade against the pool's liquidity, violating the core access-control invariant and causing direct harm to LPs who deposited under the assumption of a curated counterparty set. This constitutes broken core pool functionality and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation
The bypass requires the admin to allowlist the router address. This is a plausible and well-motivated configuration: an admin who wants allowlisted users to access routing would add the router to the allowlist, not realizing it opens the gate to every user. The misconfiguration is easy to make and the exploit path is trivially reachable once it exists. Likelihood is **medium**.

## Recommendation
The extension must check the economically relevant actor — the user who initiated the swap — not the immediate `msg.sender` of the pool. Two viable approaches:

1. **Pass the originating user in `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.
2. **Transient-storage originator**: the router writes the originating user into a well-known transient slot before calling the pool; the extension reads that slot. The pool's reentrancy guard already uses transient storage, so the pattern is established.

Either approach must be paired with a check that the `extensionData` originator field cannot be spoofed by a direct pool caller who bypasses the router.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, userA, true)   // allowlist userA
  admin calls setAllowedToSwap(pool, router, true)  // allowlist router to enable routing

Attack (userB, not allowlisted):
  userB → MetricOmmSimpleRouter.exactInputSingle({pool, ...})
    router → pool.swap(recipient, ...)          // msg.sender = router
      pool → _beforeSwap(sender=router, ...)
        extension: allowedSwapper[pool][router] == true  ✓
  swap executes for userB — allowlist bypassed

Victim (userA, allowlisted, tries to use router without router being allowlisted):
  userA → MetricOmmSimpleRouter.exactInputSingle({pool, ...})
    router → pool.swap(recipient, ...)          // msg.sender = router
      pool → _beforeSwap(sender=router, ...)
        extension: allowedSwapper[pool][router] == false
  revert NotAllowedToSwap — allowlisted user locked out of periphery
```