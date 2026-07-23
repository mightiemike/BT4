All four cited files are confirmed in the repository. Let me verify the complete call chain against the actual code.

**`SwapAllowlistExtension.beforeSwap` (L37):** [1](#0-0) 

Checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the first argument — whoever called `pool.swap()`.

**`MetricOmmPool.swap()` (L230-231):** [2](#0-1) 

Passes `msg.sender` (the immediate caller of `pool.swap()`) as `sender` to `_beforeSwap`.

**`ExtensionCalling._beforeSwap` (L160-165):** [3](#0-2) 

Forwards `sender` unchanged to every configured extension.

**`MetricOmmSimpleRouter.exactInputSingle` (L71-80):** [4](#0-3) 

The router stores `msg.sender` only in transient callback context (for payment), then calls `pool.swap()` directly. The pool's `msg.sender` is the **router address** — the original end-user address is never forwarded into the swap call.

The same pattern applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181) — all call `pool.swap()` with `msg.sender = router`. [5](#0-4) 

The Smart Audit Pivot explicitly flags this class of issue: "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and **cannot be bypassed through router**." Every element of the claim is confirmed by the production code. The exploit path is mechanically sound, the precondition (admin allowlisting the router to enable router-based access for curated users) is realistic, and no existing guard prevents it.

---

Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is whoever called `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router address, so the extension evaluates `allowedSwapper[pool][router]` rather than the actual end-user. A pool admin who allowlists the router to enable router-based access for their curated users inadvertently grants unrestricted swap access to every user of the public router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap` (L230-231). `ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))` (L160-176). `SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]` (L37), where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap()`.

`MetricOmmSimpleRouter.exactInputSingle` stores the original `msg.sender` only in transient callback context for payment (`_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn)`, L71), then calls `pool.swap()` directly (L72-80). The pool receives `msg.sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]`, never seeing the end-user's address. The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), and `exactOutput` (L165-181). No existing guard in the pool, extension, or router propagates the original caller's identity into the extension check.

Exploit flow:
1. Pool admin deploys pool with `SwapAllowlistExtension` and calls `setAllowedToSwap(pool, router, true)` to let allowlisted users reach the pool via the router.
2. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
3. Router calls `pool.swap(...)` — pool's `msg.sender` is the router.
4. Pool calls `_beforeSwap(sender=router, ...)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker successfully swaps in a pool they were explicitly excluded from.

## Impact Explanation
Any non-allowlisted user can bypass a curated pool's swap allowlist by routing through the public `MetricOmmSimpleRouter`. If the pool admin has allowlisted the router (the only way to let their curated users use the router), the allowlist provides zero protection. Pools intended to be KYC-gated, institutional, or compliance-restricted are fully open to arbitrary swappers. This is a direct policy bypass matching the contest's "Allowlist path" smart audit pivot: allowlist checks must cover the exact actor/action intended and cannot be bypassed through a router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins who deploy `SwapAllowlistExtension` and also want their allowlisted users to access the router will naturally allowlist the router address — there is no other mechanism to enable router-based swaps for curated users. Once the router is allowlisted, the bypass is unconditional and requires no special privileges: any EOA can call `exactInputSingle` on the router pointing at the curated pool.

## Recommendation
Pass the original end-user's address through the swap call chain so the extension can gate on it. One approach: add a `payer` or `originator` field to the swap parameters that the router populates with `msg.sender` before calling the pool, and have the pool forward it to extensions alongside `sender`. Alternatively, the extension can read the payer from the router's transient callback context (already stored via `_setNextCallbackContext`), but this couples the extension to a specific router implementation. At minimum, document that `SwapAllowlistExtension` is incompatible with any intermediary router and that allowlisting a router address defeats the guard entirely.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)`.
3. Attacker (not in the allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: attacker, ...})`.
4. Router executes `IMetricOmmPoolActions(pool).swap(recipient, zeroForOne, amountIn, priceLimitX64, "", extensionData)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker receives output tokens from a pool they were explicitly excluded from.

Foundry test: deploy pool + extension, allowlist router, call `exactInputSingle` from an un-allowlisted EOA, assert swap succeeds and output tokens are received.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-165)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
