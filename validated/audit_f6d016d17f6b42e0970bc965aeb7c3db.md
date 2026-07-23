Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks the router's address against the allowlist rather than the originating user's address. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently opens the gate to every user on the network, completely defeating the allowlist invariant.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- immediate caller, not originating user
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router when routed
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` and `exactInput` call `pool.swap()` directly, making the router `msg.sender` to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

This creates an irreconcilable dilemma: if the admin allowlists the router so that legitimate users can use it, the check becomes `allowedSwapper[pool][router] == true`, which passes for every user who routes through the router regardless of their identity. If the admin does not allowlist the router, allowlisted users cannot use the router at all.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks `owner` (the economic beneficiary), not `sender` (the immediate caller), because `addLiquidity` explicitly separates the operator/payer (`msg.sender`) from the position owner (`owner`). The swap path has no equivalent "originating user" field — `sender` is structurally the immediate caller.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (KYC'd users, institutional LPs, whitelisted market makers) can be fully bypassed by any unprivileged user routing through the public `MetricOmmSimpleRouter`. Unauthorized swaps execute against LP funds at oracle prices. LPs deposited under the assumption that only vetted counterparties could trade; this constitutes a direct loss of LP principal and a broken core pool invariant (allowlist-gated swap access). This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break via unprivileged path" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for the protocol. Any pool admin who enables router-based swaps for their allowlisted users must allowlist the router address, at which point the bypass is immediately available to all users. The trigger requires no special privilege, no flash loan, and no unusual token behavior — a single `exactInputSingle` or `exactInput` call through the router suffices. The condition is self-inflicted by correct admin behavior (enabling the router for legitimate users).

## Recommendation

**Short term:** In `SwapAllowlistExtension.beforeSwap`, check the `recipient` field as a proxy for the intended beneficiary, or require the pool to forward the originating EOA as a separate parameter in `extensionData`. Alternatively, document that the extension is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both a `SwapAllowlistExtension` and a router as an approved caller.

**Long term:** Redesign the `beforeSwap` hook signature to include an `origin` field passed explicitly by the router via a trusted forwarding pattern (e.g., ERC-2771 meta-transaction style), so extensions can gate on the true initiating user rather than the immediate caller. Add integration tests covering the router → pool → extension call path with mismatched allowlist entries.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured in BEFORE_SWAP_ORDER
  - Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended gated user
  - Pool admin calls setAllowedToSwap(pool, router, true)      // router allowlisted so alice can use it
  - Pool has liquidity from LPs

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool, tokenIn, amountIn, recipient=Bob, ...})
  2. Router calls pool.swap(recipient=Bob, ...) — router is msg.sender to pool
  3. Pool calls _beforeSwap(sender=router, ...)
  4. ExtensionCalling encodes sender=router and calls SwapAllowlistExtension.beforeSwap
  5. Extension checks: allowedSwapper[pool][router] == true  ✓  (router is allowlisted)
  6. Swap proceeds — Bob receives output tokens despite never being allowlisted
  7. LP funds are transferred to Bob at oracle price

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds

Foundry test sketch:
  function test_allowlistBypass() public {
    // setup pool with SwapAllowlistExtension
    // allowlist alice and router
    // attempt swap as bob through router
    // assert swap succeeds (demonstrating bypass)
  }
```