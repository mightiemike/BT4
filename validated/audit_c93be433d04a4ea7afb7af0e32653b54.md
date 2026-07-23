Looking at the code carefully to validate this claim.

Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the end user, allowing any unprivileged caller to bypass a curated pool's swap allowlist via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently voids the entire per-user allowlist: any unprivileged address can swap by calling the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to the extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))`.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is whoever called `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. The original end user (`msg.sender` of the router call) is stored only in transient storage as the payer for the callback — it is never forwarded to the extension.

A pool admin who wants to allow router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once that entry exists, every address can bypass the per-user allowlist by routing through the router, because the extension only sees the router address and approves it unconditionally. There is no other mechanism to enable router-based swaps for allowlisted users without allowlisting the router itself.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, making the router the `msg.sender` the pool and extension observe.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a whitelist of counterparties (KYC'd addresses, protocol-owned contracts, specific market makers) is fully bypassed. Any unprivileged user can swap against the pool's liquidity by calling any router `exact*` function. This constitutes broken core pool functionality: the allowlist guard fails open for all router-mediated swaps, exposing LP assets to unauthorized counterparties and potentially enabling policy-violating trades or adverse selection against the pool.

## Likelihood Explanation
The router is the standard, documented periphery entry point for swaps. Pool admins who configure a `SwapAllowlistExtension` and also want to support router-based swaps for their allowlisted users must allowlist the router — there is no other mechanism. Once the router is allowlisted, the bypass is immediately available to any address with no special privileges, no admin cooperation, and no unusual token behavior required. The attacker only needs to call a public router function. The contest's own audit targets explicitly identify this vector: "Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."

## Recommendation
The extension must gate the economic actor (the end user), not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: The router already stores `msg.sender` in transient storage as the payer. Extend this to also forward the originating user as part of `extensionData` or a dedicated field so the extension can check the real caller.

2. **Restructure the hook signature**: Add an `originator` field distinct from `sender` to the `beforeSwap` hook, populated by the pool with the end user's identity when routed through a known intermediary, or require the router to pass `msg.sender` explicitly in `extensionData` with a verifiable signature.

Until fixed, pool admins must not allowlist the router address in `SwapAllowlistExtension`; instead, they must require all allowlisted users to call `pool.swap()` directly.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for allowlisted users
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the intended allowlisted user
  - bob is NOT allowlisted

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(router, bob, ...) → ExtensionCalling forwards to extension.beforeSwap(router, bob, ...)
  4. Extension checks allowedSwapper[pool][router] → true → passes
  5. bob's swap executes against the curated pool's liquidity

Expected: revert NotAllowedToSwap (bob is not allowlisted)
Actual:   swap succeeds — allowlist is bypassed

Foundry test: deploy pool with SwapAllowlistExtension, allowlist router and alice,
assert bob's direct pool.swap() reverts NotAllowedToSwap,
then assert bob's router.exactInputSingle() succeeds — demonstrating the bypass.
```