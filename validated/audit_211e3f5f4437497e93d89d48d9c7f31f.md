Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Real Swapper, Allowing Any User to Bypass the Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is `msg.sender` from `MetricOmmPool.swap()` — the direct caller of the pool, not the originating end-user. When swaps are routed through `MetricOmmSimpleRouter`, `sender` becomes the router address. Any pool admin who allowlists the router (a necessary step for legitimate users to trade via the router) simultaneously opens the gate to every non-allowlisted address, fully defeating the allowlist policy.

## Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap(), i.e. the router
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that argument against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without encoding the original `msg.sender` anywhere the extension can verify:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData   // ← caller-controlled, forgeable
    );
```

When `bob` calls `router.exactInputSingle()`, the pool receives `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`. If the admin has allowlisted the router (required for any legitimate user to trade via the router), this check passes for every caller regardless of their identity. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (KYC'd counterparties, whitelisted market makers, protocol-internal actors) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The non-allowlisted user executes a real swap against pool liquidity, receiving output tokens and paying input tokens through the router callback. This is a direct policy bypass with fund-impacting consequences for LP positions on curated pools, constituting broken core pool functionality causing loss of funds or unusable access controls.

## Likelihood Explanation

The bypass requires no special privileges, no flash loan, and no multi-transaction setup. `MetricOmmSimpleRouter` is a public, permissionless contract. The only precondition — that the pool admin has allowlisted the router address — is the normal operational configuration for any curated pool that wants to support the standard periphery. The admin faces an impossible choice: either block all router-mediated swaps (including for allowlisted users) or allowlist the router and expose the pool to all callers.

## Recommendation

The extension must gate on the original end-user, not the direct caller of `pool.swap()`. The cleanest fix is for the pool core to forward a verified `initiator` field (analogous to the `owner`/`sender` split already present on `addLiquidity`) through the hook signature. Alternatively, the router can encode `msg.sender` into `extensionData` using a signed or factory-verified scheme, and the extension can decode and verify it — though caller-controlled `extensionData` as currently implemented is forgeable and cannot be trusted for access control without an additional authentication layer.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowlisted
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use it

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) with msg.sender = router
  - Pool calls extension.beforeSwap(sender=router, ...)
  - Check: allowedSwapper[pool][router] == true → passes
  - Bob's swap executes against pool liquidity, bypassing the allowlist

Call trace:
bob → MetricOmmSimpleRouter.exactInputSingle()          [MetricOmmSimpleRouter.sol L67]
        → MetricOmmPool.swap(recipient, ...)             [msg.sender = router, MetricOmmPool.sol L224]
            → _beforeSwap(sender=router, ...)            [MetricOmmPool.sol L230]
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)  [SwapAllowlistExtension.sol L37]
                    allowedSwapper[pool][router] == true → no revert
            → _executeSwap(...)                          [bob's trade executes]
```