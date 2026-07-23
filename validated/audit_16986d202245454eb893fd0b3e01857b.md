Audit Report

## Title
`SwapAllowlistExtension` checks the immediate caller (`sender`) instead of the originating user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `sender`, so the extension checks the router's allowlist status rather than the originating user's. Any pool admin who allowlists the router to support normal UX inadvertently grants every on-chain address the ability to bypass the swap allowlist, exposing restricted LP liquidity to non-vetted counterparties.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L149-177
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any `exact*` variant), the router calls `pool.swap()` at line 72-80 of `MetricOmmSimpleRouter.sol`, making `sender = address(router)`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma: if the router is not allowlisted, no user can swap through it; if the router is allowlisted (the natural operational step for any pool supporting standard UX), every address on-chain can bypass the allowlist by calling the public, permissionless `MetricOmmSimpleRouter`.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the position owner, explicitly passed by the caller) rather than `sender` (the immediate caller):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit extension gates the right identity regardless of whether the liquidity adder is used. The swap extension does not.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for KYC compliance, private market-making, or institutional access control is fully bypassable by any user routing through the public `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against restricted LP liquidity, draining LP capital at oracle-anchored prices the pool admin intended to reserve for vetted counterparties. This breaks the core pool access-control invariant and constitutes broken core pool functionality with direct LP fund exposure. The wrong value is the `sender` identity passed to and checked by the extension — it is the router address rather than the economically relevant user address.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router, which is the natural operational step for any pool that wants to support standard UX. `MetricOmmSimpleRouter` is a public, permissionless contract. Any user who calls it on a router-allowlisted pool bypasses the guard unconditionally. No special privileges, flash loans, or oracle manipulation are required. The condition is realistic and expected in production deployments.

## Recommendation
Gate on the originating user, not the immediate caller. Two viable approaches:

1. **Mirror the deposit allowlist pattern using `recipient`**: Check `recipient` (the address that receives output tokens) instead of `sender`, since `recipient` is the economically relevant actor for a swap and is always set to the actual user even when routing through `MetricOmmSimpleRouter`.

2. **Pass user identity through `extensionData`**: Have the router encode the originating user's address in `extensionData` and have the extension decode and verify it, trusting only known routers as `msg.sender` of the pool call.

## Proof of Concept
```
1. Pool P is deployed with SwapAllowlistExtension E.
2. Pool admin calls E.setAllowedToSwap(P, alice, true)       // alice is KYC'd
3. Pool admin calls E.setAllowedToSwap(P, router, true)      // enable router UX
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(P, ...)
5. Router calls P.swap(recipient=bob, ...)  [MetricOmmSimpleRouter.sol L72-80]
6. P passes msg.sender=router as `sender` to _beforeSwap     [MetricOmmPool.sol L230-231]
7. E.beforeSwap checks allowedSwapper[P][router] == true → passes [SwapAllowlistExtension.sol L37]
8. Bob's swap executes against restricted LP liquidity.
```

Foundry test plan: deploy a pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, then call `MetricOmmSimpleRouter.exactInputSingle` as `bob` (not allowlisted) and assert the swap succeeds — demonstrating the bypass.