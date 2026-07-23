Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Originating User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()` — the immediate caller, not the economic actor. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract address. A pool admin who allowlists the router to enable router-mediated swaps for their allowlisted users inadvertently grants every user the ability to bypass the allowlist, because the router is a public, permissionless contract.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, ...)
    )
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`.

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

The router does not forward the originating user's identity. `msg.sender` in `pool.swap()` is the router address. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

A pool admin who wants allowlisted users to be able to use the router must allowlist the router address. But `MetricOmmSimpleRouter` is a public, permissionless contract — any address can call it. Allowlisting the router therefore grants every user the ability to swap in the restricted pool, defeating the allowlist entirely. There is no way for the pool admin to achieve "allow my allowlisted users to use the router" without simultaneously allowing every user to bypass the allowlist.

## Impact Explanation
Any user can bypass a pool's swap allowlist by routing through `MetricOmmSimpleRouter`. If the pool admin has restricted swaps for economic reasons (e.g., to prevent adversarial flow, enforce counterparty vetting, or comply with regulatory requirements), the bypass allows unrestricted trading against LP positions. LPs in such pools suffer the exact adverse-selection or compliance exposure the allowlist was meant to prevent. This constitutes an admin-boundary break where an unprivileged path circumvents an admin-configured access control, with direct fund-impacting consequences for LPs.

## Likelihood Explanation
Medium. The bypass is only reachable after the pool admin allowlists the router — a deliberate but reasonable action for any admin who wants their allowlisted users to benefit from the router's UX (deadline checks, multi-hop, slippage protection, etc.). The misunderstanding is natural: the admin believes the allowlist still gates individual users when the router is allowlisted, but it does not. Once the router is allowlisted, the trigger is fully unprivileged and requires no further admin cooperation.

## Recommendation
The extension must gate the economic actor, not the immediate caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a coordinated change to the router and the extension, and the extension must reject calls where `extensionData` is absent or malformed.

2. **Document that the router must never be allowlisted on restricted pools**: If the design intent is that router-mediated swaps are simply incompatible with the allowlist, this must be stated explicitly in the extension's NatSpec and enforced by the factory or a deployment check.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured; sets `allowAllSwappers[pool] = false`.
2. Admin allowlists `user1`: `allowedSwapper[pool][user1] = true`.
3. Admin allowlists the router so `user1` can use it: `allowedSwapper[pool][router] = true`.
4. `user2` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)` — `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → no revert.
7. `user2` successfully swaps in the restricted pool, bypassing the allowlist entirely.