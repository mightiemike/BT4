Audit Report

## Title
`SwapAllowlistExtension` Gates on Direct Pool Caller Instead of Originating User, Enabling Router-Mediated Bypass of Per-User Allowlist - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` inside `MetricOmmPool.swap()` — the direct caller of the pool, not the originating EOA. When swaps route through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension sees `sender = address(router)`. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every public user access to the curated pool, completely defeating the per-user allowlist invariant.

## Finding Description

**Root cause — extension checks direct caller, not originating user:**

`MetricOmmPool.swap()` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // direct caller of pool.swap()
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this unchanged as the first argument to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L162-165
abi.encodeCall(
  IMetricOmmExtensions.beforeSwap,
  (sender, ...)
)
```

`SwapAllowlistExtension.beforeSwap` then gates on this value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

**Router path — router becomes `sender`:**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool's `msg.sender` is `address(router)`, so the extension receives `sender = address(router)` and checks `allowedSwapper[pool][address(router)]`. There is no mechanism in the router or pool to forward the originating EOA's address as `sender` to the extension.

**Two broken scenarios:**

1. **Bypass path**: Admin calls `setAllowedToSwap(pool, address(router), true)` to enable router-mediated swaps. Any unprivileged user can now call `router.exactInputSingle(...)` and the extension passes because `allowedSwapper[pool][router] == true`, regardless of whether the user was ever individually authorized.

2. **Broken path**: Admin allowlists individual EOA addresses. Those users cannot swap through the router at all because the extension checks `allowedSwapper[pool][router]` (false), making the router completely unusable on curated pools.

**Test confirms the design flaw** — the test allowlists `address(callers[0])` (the `TestCaller` intermediate contract), not `users[0]` (the originating EOA), confirming the extension was designed around direct callers only:

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L70-73
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
```

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd addresses, whitelisted market makers, or protocol-owned contracts can be fully bypassed by any public user routing through `MetricOmmSimpleRouter`. The attacker can execute unrestricted swaps on a pool whose entire security model depends on the allowlist being enforced, draining LP value through unauthorized trades. This is a direct loss of LP principal and a complete failure of the pool's core access-control invariant — matching the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact categories.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported periphery entry point for swaps. Any pool admin who deploys a curated pool and then allowlists the router (the natural step to enable router-mediated swaps for legitimate users) immediately triggers the bypass. The attacker requires no special setup: a single public `exactInputSingle` or `exactInput` call through the router suffices. The condition is reachable on any production pool using `SwapAllowlistExtension` with router support enabled.

## Recommendation

The extension must gate on the originating user, not the direct pool caller. Two approaches:

1. **Pass originating user through the call chain**: `MetricOmmPool.swap()` should accept an explicit `originSender` parameter populated by the router with `msg.sender` (the EOA), and forward it to extensions as `sender` instead of `msg.sender` inside the pool.

2. **Attest originating user via `extensionData`**: The extension can require that an explicit sender field embedded in `extensionData` (attested by the router) is allowlisted, with the pool or router signing/attesting to the original user identity.

Additionally, `DepositAllowlistExtension.beforeAddLiquidity` should be audited for the analogous issue on the `owner` vs. payer separation path through `MetricOmmPoolLiquidityAdder`, where `sender` (the adder contract) and `owner` (the position recipient) are already separated — the extension correctly gates on `owner` there, but the same router-mediation analysis should be applied.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, address(MetricOmmSimpleRouter), true)
     — intended to let legitimate users swap via the router.
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. Attacker (any EOA) calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...) — pool's msg.sender = address(router).
  6. Pool calls _beforeSwap(msg.sender=router, ...) → extension.beforeSwap(sender=router, ...).
  7. Extension checks: allowedSwapper[pool][address(router)] == true → passes.
  8. Swap executes. Attacker trades on a pool they were never authorized to access.

Result:
  allowedSwapper[pool][attacker] == false, yet the swap succeeds because
  the extension sees sender = address(router), which is allowlisted.
  The per-user allowlist is completely bypassed for all router-mediated swaps.

Foundry test plan:
  - Deploy pool with SwapAllowlistExtension.
  - setAllowedToSwap(pool, address(router), true).
  - Assert attacker EOA (not individually allowlisted) can successfully swap via router.
  - Assert allowedSwapper[pool][attacker] == false (confirming bypass, not legitimate access).
```