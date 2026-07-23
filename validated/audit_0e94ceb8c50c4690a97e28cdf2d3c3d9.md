Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks the router's address rather than the end-user's. Allowlisting the router — a necessary operational step for any router-based swap — opens the gate to every address on-chain, completely defeating the per-user access control.

## Finding Description

**Root cause — `SwapAllowlistExtension.beforeSwap()` (L37):**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct key). `sender` is the argument forwarded from the pool — which is whoever called `pool.swap()`.

**`MetricOmmPool.swap()` sets `sender = msg.sender` (L230–240):**

```solidity
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

When the user goes through the router, `msg.sender` to the pool is the router contract, not the user.

**`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly (L72–80):**

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., "", params.extensionData);
```

No mechanism forwards the original `msg.sender` (the end-user) into the pool's `sender` argument. The pool receives `msg.sender = router`.

**`ExtensionCalling._beforeSwap()` forwards `sender` verbatim** into `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))`, so the router address propagates unchanged into the extension check.

**Exploit path:**

| Step | Actor | Call | Result |
|---|---|---|---|
| Setup | Admin | `setAllowedToSwap(pool, userA, true)` | userA allowlisted |
| Setup | Admin | `setAllowedToSwap(pool, router, true)` | router allowlisted (required for userA to use router) |
| Attack | userB (not allowlisted) | `router.exactInputSingle({pool, ...})` | router calls `pool.swap()` with `msg.sender = router` |
| Check | Extension | `allowedSwapper[pool][router] == true` | passes — swap executes for userB |

The admin faces an impossible choice: not allowlisting the router blocks even individually allowlisted users from using it; allowlisting the router opens the pool to every address. There is no configuration that achieves "only allowlisted users may use the router."

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all entry points call `pool.swap()` with `msg.sender = router`.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties, institutional participants). Once the router is allowlisted, any unprivileged address can call any router entry point and execute swaps against the restricted pool. The allowlist is completely defeated. Unauthorized users can trade at oracle-quoted prices against a pool designed to be access-controlled, constituting broken core pool functionality and a direct bypass of an admin-configured access boundary by an unprivileged path.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router — a routine operational step any admin would take when they want their allowlisted users to be able to use the standard periphery. The admin has no reason to suspect this opens the gate to all users; the extension's name and interface imply per-address gating. The bypass is then reachable by any unprivileged address with no special setup, no capital requirement beyond the swap input, and is repeatable indefinitely.

## Recommendation

The extension must gate the **economic actor** (end-user), not the **call-chain intermediary** (router). The simplest safe fix:

1. **Pass end-user identity through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it only when `sender` is a known trusted router (registry). For direct calls, fall back to checking `sender` directly.
2. **Dedicated router variant:** A new router that passes the original caller as a verified field the extension can trust, authenticated via a known router registry.

Option 1 combined with a trusted-router registry is the minimal change: the extension checks `sender` directly for non-router callers, and decodes the real user from `extensionData` when `sender` is a registered trusted router.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order)
  admin: setAllowedToSwap(pool, userA, true)
  admin: setAllowedToSwap(pool, router, true)
    (necessary so userA can use the router)

Attack (userB, not allowlisted):
  userB calls router.exactInputSingle({pool: pool, recipient: userB, ...})
    → router calls pool.swap(userB, zeroForOne, amount, limit, "", "")
      msg.sender to pool = router
    → pool calls _beforeSwap(sender=router, ...)
    → ExtensionCalling forwards sender=router to SwapAllowlistExtension.beforeSwap
      checks: allowedSwapper[pool][router] == true  ✓
    → swap executes, userB receives output tokens

Result: userB bypassed the per-user allowlist and executed a swap
        in a pool intended to be restricted to userA only.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension in beforeSwap order.
  2. allowedSwapper[pool][userA] = true; allowedSwapper[pool][router] = true.
  3. vm.prank(userB); router.exactInputSingle(...) — assert succeeds.
  4. vm.prank(userB); pool.swap(...) directly — assert reverts NotAllowedToSwap.
     (confirms bypass is router-specific, not a general allowlist failure)
```