Audit Report

## Title
SwapAllowlistExtension Gates Router Address Instead of End-User, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to its own `msg.sender` — the router contract — when a swap is routed through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable router-based swaps for legitimate users, every caller of the public router bypasses the allowlist gate, including addresses the admin explicitly never allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- pool's msg.sender, i.e. the router when routed
    recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to each configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`.

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`. Once the admin adds the router to `allowedSwapper` (the necessary step to let any allowlisted user trade via the router UI), the check returns `true` for every caller of the router — including addresses the admin never allowlisted. The router itself (`MetricOmmSimpleRouter`) provides no mechanism to forward the originating user's address to the pool or extension; it calls `pool.swap(recipient, ...)` with no caller identity field.

## Impact Explanation
Any address excluded from the allowlist can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter`. Pools that use `SwapAllowlistExtension` to restrict trading to specific market makers — the primary stated purpose of the extension — are fully bypassed. LP principal is at direct risk: the oracle-anchored pool settles trades at the oracle price regardless of counterparty identity, and the allowlist is the sole mechanism preventing harmful counterparties from trading against LPs at those prices. This constitutes a direct loss of LP principal above Sherlock thresholds (broken core access-control causing fund loss).

## Likelihood Explanation
The router is a public, permissionless contract. Any pool that enables router-based swaps for its allowlisted users must add the router to `allowedSwapper`, which simultaneously opens the pool to all users. The misconfiguration is not obvious: the admin's intent ("allow my users to use the router") and the effect ("allow everyone") are indistinguishable at the setter call site. No special privilege or front-running is required; any EOA can call the router.

## Recommendation
The extension must resolve the true end-user identity rather than the intermediary's address. Two options:

1. **Forward the originating user through `extensionData`.** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension decodes and verifies this value. To prevent spoofing, the pool must also pass the direct caller (router address) so the extension can require that the decoded user comes from a trusted router.

2. **Pool-level trusted-forwarder pattern.** Introduce an explicit `caller` field in the pool's `swap` signature that the pool passes to extensions alongside its own `msg.sender`, allowing the extension to gate on the true originator when the direct caller is a known trusted router.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` and calls `setAllowedToSwap(pool, alice, true)`.
2. Admin calls `setAllowedToSwap(pool, MetricOmmSimpleRouter, true)` so that `alice` can use the router UI.
3. `bob` (never allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. The router calls `pool.swap(recipient, ...)`. Pool's `msg.sender` = router address.
5. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true`.
7. `bob`'s swap executes successfully, bypassing the allowlist entirely.

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `exactInputSingle` from `bob`, assert no revert and that `bob` receives output tokens.