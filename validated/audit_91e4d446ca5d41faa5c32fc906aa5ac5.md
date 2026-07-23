Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `MetricOmmPool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` becomes the router contract address. Any pool admin who allowlists the router (required for router-mediated swaps on an allowlisted pool) inadvertently grants every unprivileged user the ability to bypass the allowlist by calling any of the router's public entry points.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,
  recipient,
  ...
  extensionData
);
```

`ExtensionCalling._beforeSwap` then ABI-encodes that value as the `sender` argument in the call to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
  )
);
```

**Step 2 — The extension checks `sender`, which is the router, not the end user.**

`SwapAllowlistExtension.beforeSwap` receives `sender` (the router) and checks it against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller), and `sender` is whatever address called `pool.swap`.

**Step 3 — The router calls `pool.swap` directly, making itself the `msg.sender`.**

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` with no mechanism to forward the original caller's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
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

The `msg.sender` stored in transient storage via `_setNextCallbackContext` is used only for the payment callback, not forwarded to extensions. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Structural mismatch:** For direct swaps, `sender = end user` — the check is correct. For router-mediated swaps, `sender = router` — the check is against the router address, not the end user. A pool admin who wants to support router-mediated swaps for their allowlisted users must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, every user — allowlisted or not — can bypass the guard by calling any of the router's public entry points.

## Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC'd users, institutional partners, or whitelisted bots) is fully open to any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can drain LP-owned inventory at oracle prices, extract spread fees, or manipulate the pool's bin cursor — all actions the allowlist was configured to prevent. This constitutes a direct loss of LP principal and a broken core pool invariant. The extension's own NatSpec states it "Gates `swap` by swapper address, per pool" — this invariant is violated the moment the router is added to the allowlist.

## Likelihood Explanation

Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants their allowlisted users to benefit from multi-hop routing must allowlist the router. There is no alternative path: the router is a public, permissionless contract with no access control of its own. The bypass is therefore reachable by any unprivileged user on every pool that supports router-mediated swaps alongside an allowlist. The scenario is explicitly identified as a known audit target in the repository's own test generation script (swap allowlist gate vector).

## Recommendation

The extension must gate the economic actor (the end user), not the transport layer (the router). Two viable approaches:

1. **Extension-data forwarding**: Require the router to encode the original `msg.sender` into `extensionData` for each hop, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router.
2. **Sender override in the pool interface**: Add an optional `originalSender` field to the swap call that the pool passes to extensions, populated by the router from its own `msg.sender` at entry.

Until one of these is implemented, pool admins must choose between supporting router-mediated swaps and enforcing a meaningful swap allowlist — they cannot have both.

## Proof of Concept

```
Setup:
  pool  = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin = pool admin

Step 1 — Admin allowlists user1 for direct swaps:
  admin calls swapExtension.setAllowedToSwap(pool, user1, true)

Step 2 — Admin allowlists the router so user1 can use multi-hop routing:
  admin calls swapExtension.setAllowedToSwap(pool, router, true)

Step 3 — user2 (not allowlisted) bypasses the guard:
  user2 calls router.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
  → pool calls _beforeSwap(msg.sender=router, ...)
  → SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  → swap executes; user2 trades on the curated pool without authorization

Invariant violated:
  allowedSwapper[pool][user2] == false, yet user2's swap settles successfully.

Foundry test plan:
  1. Deploy SwapAllowlistExtension and a MetricOmmPool with it as beforeSwap hook.
  2. Deploy MetricOmmSimpleRouter.
  3. Admin calls setAllowedToSwap(pool, router, true).
  4. Call router.exactInputSingle from an address not in the allowlist.
  5. Assert the swap succeeds (no NotAllowedToSwap revert).
  6. Assert allowedSwapper[pool][attacker] == false to confirm the bypass.
```