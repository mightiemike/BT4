Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates pool swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to all users (Mode A — bypass), while a pool admin who allowlists individual users blocks those users from swapping through the router (Mode B — DoS).

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks this `sender` against the allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without forwarding the original caller:

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

The pool receives `msg.sender = router`. It passes `sender = router` to `_beforeSwap`. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Mode A — Allowlist bypass:** If the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the allowlist by routing through `MetricOmmSimpleRouter`. The extension sees `allowedSwapper[pool][router] = true` and passes unconditionally.

**Mode B — Legitimate user DoS:** If the pool admin allowlists individual users (not the router), those users are blocked from swapping through the router because `allowedSwapper[pool][router] = false`, even though they are explicitly permitted.

The `DepositAllowlistExtension` does not share this flaw: it ignores `sender` and checks `owner` (the position owner explicitly passed by the caller), which correctly identifies the economically relevant actor regardless of intermediary.

## Impact Explanation

Mode A is the higher-severity path. `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who enables router support by allowlisting the router address has effectively disabled the allowlist for all users. Any disallowed address can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` and the extension will pass because `allowedSwapper[pool][router] = true`. This is a direct, complete bypass of the curated-pool access control — an admin-boundary break reachable by any unprivileged EOA with no additional preconditions. Mode B breaks core swap functionality for legitimate users on curated pools that rely on the router.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the documented, supported swap entrypoint for EOAs. Pool admins who deploy a `SwapAllowlistExtension` and want to support router-mediated swaps will naturally call `setAllowedToSwap(pool, router, true)`, triggering Mode A. No privileged access is required by the attacker: any EOA can call the router. The bypass is deterministic and requires no timing, oracle manipulation, or multi-step setup.

## Recommendation

The extension must check the original user, not the intermediary. Two options:

1. **Pass the original user through `extensionData`**: the router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention and that the extension only accepts this encoding from known routers.
2. **Add a `swapper` field to the pool's `swap` signature**: the pool accepts an explicit `swapper` address (validated against `msg.sender` or a trusted forwarder list) and passes it to `_beforeSwap`. The extension checks that field instead of `sender`.

The `DepositAllowlistExtension` pattern — checking `owner`, not `sender` — is the correct model: gate the economically relevant actor, not the intermediary.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. A non-allowlisted user Alice calls `router.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] = true` → passes.
7. Alice's swap executes on the curated pool despite never being allowlisted.

Conversely, if the admin calls `setAllowedToSwap(pool, alice, true)` (not the router), Alice's direct pool call succeeds but her router call fails because `allowedSwapper[pool][router] = false`.