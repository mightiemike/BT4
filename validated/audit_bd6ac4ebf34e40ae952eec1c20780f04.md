Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as `sender` instead of actual end-user, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the pool's `msg.sender` — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any pool admin who allowlists the router (required to support router-mediated swaps for legitimate users) inadvertently grants every unprivileged user the ability to bypass the per-user gate.

## Finding Description
In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When `MetricOmmSimpleRouter.exactInputSingle` is called by an end-user, it calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. If the router is allowlisted — which is structurally required for any allowlisted user to swap via the router — every user, including those explicitly not on the allowlist, passes the check.

No existing guard in `SwapAllowlistExtension`, `MetricOmmPool`, or `MetricOmmSimpleRouter` recovers the original end-user identity. The `extensionData` field is user-supplied and unverified, so it cannot be trusted for identity.

## Impact Explanation
A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties). To allow any allowlisted user to swap via the router, the admin must call `setAllowedToSwap(pool, router, true)`. Once this is done, any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle(...)`, causing the extension to see `sender = router` (allowlisted), and the swap proceeds. The per-user allowlist is completely bypassed, allowing unauthorized users to execute swaps against a restricted pool — draining liquidity or extracting value at oracle-anchored prices intended only for specific counterparties. This constitutes a broken core pool access-control mechanism with direct loss of funds potential.

## Likelihood Explanation
- `MetricOmmSimpleRouter` is a public, permissionless contract callable by any address.
- Any pool using `SwapAllowlistExtension` that also supports router-mediated swaps for its allowlisted users **must** allowlist the router, automatically creating the bypass condition.
- No privileged access, special configuration, or malicious setup is required beyond the normal operational state of such a pool.
- The attack is repeatable and requires only a standard `exactInputSingle` call.

## Recommendation
The extension must check the identity of the true economic actor, not the intermediate caller. Options:
1. The pool's `swap` function should accept an explicit `originator` parameter (the true end-user), with the router passing its own `msg.sender` into that field — analogous to how `DepositAllowlistExtension` checks `owner` (explicitly provided by the caller) rather than `sender`.
2. Alternatively, the router encodes the original `msg.sender` into `extensionData` and the extension decodes and verifies it via a trusted router registry, though this requires additional trust infrastructure.
3. The simplest safe default: document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and enforce this at the factory level by rejecting pools that configure both.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` registered in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap for allowlisted users.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(router, recipient, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Attacker successfully swaps against the restricted pool, bypassing the per-user allowlist.

Foundry test: deploy pool with extension, allowlist only the router, assert that a non-allowlisted EOA calling `exactInputSingle` succeeds (demonstrating the bypass), then assert the same EOA calling `pool.swap` directly reverts with `NotAllowedToSwap`.