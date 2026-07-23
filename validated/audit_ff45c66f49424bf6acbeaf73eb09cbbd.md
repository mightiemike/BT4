Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the real user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. If the router is allowlisted (the natural production configuration for a pool that accepts router-mediated swaps), every unprivileged user bypasses the per-user gate entirely.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

When called via `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user. `SwapAllowlistExtension.beforeSwap` then checks that argument against the allowlist: [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. `MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no mechanism to forward the original `msg.sender`: [3](#0-2) 

The same pattern applies to `exactInput` (L103–112), `exactOutputSingle` (L135–137), and `exactOutput` (L165–181). No existing guard in `SwapAllowlistExtension` or `MetricOmmPool` compensates for this mismatch.

## Impact Explanation
**Scenario A (router is allowlisted — natural production setup):** A pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to a curated set of users and also allowlists the router so those users can interact via the standard periphery. Because the extension checks `allowedSwapper[pool][router]` and the router is allowlisted, every user — including those explicitly not on the allowlist — can swap by routing through `MetricOmmSimpleRouter`. The per-user gate is completely nullified. This is a broken access-control invariant: the pool admin's intent to restrict swaps to specific users is bypassed by an unprivileged path, fitting the "Admin-boundary break" and "Broken core pool functionality" allowed impact categories.

**Scenario B (only individual users are allowlisted):** Allowlisted users who attempt to swap through the router are blocked (the router is not on the allowlist), while they can only swap by calling the pool directly. This breaks the expected UX and makes the standard swap flow unusable for allowlisted users.

## Likelihood Explanation
The trigger requires only: (1) a pool configured with `SwapAllowlistExtension` in its `beforeSwap` order (a supported, documented extension), and (2) any user calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) on that pool. No privileged access, no malicious setup, and no non-standard tokens are required. The router is a public, permissionless contract. Scenario A is the most likely production configuration because allowlisting the router is the obvious way to let curated users interact via the standard periphery.

## Recommendation
The `sender` argument passed to `beforeSwap` must represent the actual end user, not the intermediary. Two complementary fixes:

1. **In `MetricOmmSimpleRouter`:** pass the original `msg.sender` as an explicit field in `extensionData` so extensions can decode it. The extension would then read the real user from `extensionData` rather than from the `sender` argument.
2. **In `SwapAllowlistExtension`:** decode the real user from `extensionData` when present, falling back to `sender` for direct pool calls.

Alternatively, redesign the `beforeSwap` interface to include a dedicated `originator` field that the pool populates from a trusted source (e.g., transient storage set by the router before calling the pool), so extensions always see the human-level actor regardless of routing depth.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as extension1, beforeSwap order = [1]
  - allowedSwapper[pool][router] = true   (admin allowlists the router)
  - allowedSwapper[pool][alice]  = false  (alice is NOT allowlisted)

Attack:
  alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(...)          // msg.sender = router
    → pool calls _beforeSwap(router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] == true
    → swap proceeds                        // alice bypassed the allowlist

Expected: revert NotAllowedToSwap()
Actual:   swap succeeds
```

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
