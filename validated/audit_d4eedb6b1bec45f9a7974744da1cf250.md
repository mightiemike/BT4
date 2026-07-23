Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of original user, allowing any user to bypass per-user swap restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to every extension hook. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract. A pool admin who allowlists the router to enable legitimate users to use the standard periphery path simultaneously opens the gate to every other user, completely defeating per-user curation enforced by `SwapAllowlistExtension`.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to `_beforeSwap`.**

In `MetricOmmPool.swap`, the hook is called as:

```solidity
_beforeSwap(
    msg.sender,   // always the immediate caller of the pool
    recipient,
    ...
);
``` [1](#0-0) 

**Step 2 — `SwapAllowlistExtension.beforeSwap` keys the allowlist on that `sender`.**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

`msg.sender` here is the pool (correct pool-keying), but `sender` is the pool's `msg.sender` — the router when the user goes through the periphery.

**Step 3 — `MetricOmmSimpleRouter` calls the pool directly; the original user's address is never forwarded.**

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The original user (`msg.sender` of the router) is stored only in transient callback context for payment settlement; it is never passed to the pool's `swap` call. The pool therefore sees `msg.sender = router`.

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][original_user]`.**

The admin's only options are:
- **Do not allowlist the router** → intended allowlisted users cannot use the standard periphery path at all.
- **Allowlist the router** → every user on the network can bypass the per-user restriction by routing through `MetricOmmSimpleRouter`.

No configuration simultaneously allows intended users to use the router and blocks unintended users.

## Impact Explanation

Any unprivileged user can trade on a pool whose admin intended to restrict access to a curated set of counterparties (KYC, whitelist-only, institutional-only pools). The attacker calls `MetricOmmSimpleRouter.exactInputSingle` targeting the restricted pool. If the router is allowlisted (the only way for legitimate users to use the periphery), the `beforeSwap` hook passes unconditionally for the attacker. LP principals are at risk because the pool's risk model was designed for a known, trusted set of traders. This constitutes a direct loss of LP principal reachable by any unprivileged caller through the standard public router path. **Severity: High.**

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical periphery entry point. Any pool admin who wants their allowlisted users to use the standard UX must allowlist the router. The moment they do, the bypass is open to everyone. The attacker needs no special privileges, no tokens pre-positioned, and no admin cooperation — only the ability to call a public function. The condition (router allowlisted) is the expected production configuration for any pool using `SwapAllowlistExtension` with the standard periphery.

## Recommendation

1. **Forward the original user through the router.** Add an explicit `address sender` parameter to `IMetricOmmPoolActions.swap` so the pool can pass the true originator to extension hooks instead of its own `msg.sender`.

2. **Alternatively, have the extension decode the original user from `extensionData`.** The router would ABI-encode `msg.sender` into `extensionData`; the extension would decode and check it. This requires the extension to trust only calls whose `extensionData` is well-formed, which can be enforced by checking that `msg.sender` (the pool) is a known factory pool.

3. **Document the limitation clearly** in `SwapAllowlistExtension` NatDoc: allowlisting the router grants access to all router users, not just the intended subset.

## Proof of Concept

```
Setup:
  pool = curated MetricOmmPool with SwapAllowlistExtension configured
  admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the UI

Attack:
  charlie (not allowlisted) calls:
    MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        tokenIn: token0,
        amountIn: large_amount,
        ...
    })

  Flow:
    router.exactInputSingle → pool.swap(msg.sender=router, ...)
    pool._beforeSwap(sender=router, ...)
    SwapAllowlistExtension.beforeSwap:
      allowedSwapper[pool][router] == true  ← admin set this for alice's benefit
      → hook passes
    charlie's swap executes at oracle price

Result:
  charlie bypassed the per-user allowlist entirely.
  alice's individual allowlist entry is irrelevant; the router entry is the effective gate.
  Any user can repeat this attack indefinitely.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
