The code confirms all cited references. Let me verify the key facts:

1. `MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap` — confirmed at lines 230–231.
2. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()` — confirmed at line 37.
3. `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` from the pool's perspective — confirmed at lines 72–80.
4. `exactInput` does the same for every hop — confirmed at lines 104–112.

The bypass path is real, reachable by any unprivileged user, and directly contradicts the stated purpose of `SwapAllowlistExtension`. The Smart Audit Pivots explicitly flag this class of issue: "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through router."

---

Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the economic actor, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` at the time `pool.swap()` is called — the router contract, not the end user. When a pool admin allowlists the router so that permitted users can trade through the standard UX path, the allowlist check resolves to `allowedSwapper[pool][router] == true` for every caller, including addresses the admin explicitly excluded. Any unprivileged user can bypass the per-pool swap allowlist by routing through `MetricOmmSimpleRouter`.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-231
_beforeSwap(
  msg.sender,   // ← direct caller of pool.swap(), not the economic actor
  ...
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension, including `SwapAllowlistExtension`. The extension checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(
    params.recipient,
    params.zeroForOne,
    ...
  );
```

At that point `msg.sender` inside the pool is the router, so `sender` delivered to the extension is the router address. The allowlist lookup becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same substitution occurs for every hop in `exactInput` multi-hop paths (lines 104–112). No mechanism exists to recover the originating user's address from within the extension.

Existing guards are insufficient: the only check in `beforeSwap` is the `allowedSwapper` mapping keyed on `sender`, and there is no fallback to `tx.origin` or any forwarded-identity mechanism in the router or extension.

## Impact Explanation
A pool admin who deploys `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses (e.g., trusted counterparties for an institutional pool). To allow those addresses to trade via the standard router UX, the admin must add the router to the allowlist. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and every caller of any `exact*` router function passes the check regardless of their own allowlist status. Unauthorized users can trade against LP positions that were meant to be accessible only to trusted counterparties, extracting value from LPs who accepted risk under the assumption of a restricted trading set. This is a broken core pool functionality / admin-boundary break with direct fund impact on LPs.

## Likelihood Explanation
The router is the standard, documented entry point for swaps. A pool admin enabling router-based trading for allowlisted users will naturally add the router to the allowlist — this is the only way to make the feature work. The bypass requires no special privilege, no malicious setup, no non-standard token, and no flash loan. Any user who observes the router is allowlisted (readable on-chain via `allowedSwapper`) can exploit this immediately and repeatedly.

## Recommendation
The `beforeSwap` hook must gate the economic actor, not the immediate caller. Two complementary approaches:

1. **Short term:** Require the router to forward the originating `msg.sender` in `extensionData` and verify it in `SwapAllowlistExtension.beforeSwap`. The extension should decode the caller identity from `extensionData` when `sender` is a known router, and check `allowedSwapper[pool][decodedCaller]`.

2. **Long term (preferred):** Redesign the router to pass the originating user as the `sender` argument to `pool.swap`, or add a dedicated `swapFor(address onBehalfOf, ...)` entry point. The pool should record that canonical identity and pass it to extensions. The allowlist extension should then gate that canonical identity regardless of call path.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  admin calls setAllowedToSwap(pool, router, true)  // router added so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ..., recipient: bob})

Execution trace:
  router.exactInputSingle()                          // msg.sender = bob
    → pool.swap(recipient=bob, ...)                  // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes, bob receives tokens

Result: bob bypasses the allowlist and trades on a restricted pool.
```

The allowlist check resolves to `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][bob]`, so the guard is silently satisfied and the swap settles in full. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
