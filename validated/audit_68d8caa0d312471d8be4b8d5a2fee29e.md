Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the end-user, allowing complete per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument forwarded by the pool, which is always `msg.sender` at the pool level — the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This makes the per-user allowlist either completely bypassable (if the router is allowlisted) or completely blocking for legitimate users (if it is not), with no configuration that achieves the intended per-user access control.

## Finding Description

**Root cause:** `SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` at the time `pool.swap()` is called.

In `MetricOmmPool.swap()`: [1](#0-0) 

`msg.sender` here is whoever called `pool.swap()`. When the call originates from `MetricOmmSimpleRouter.exactInputSingle`: [2](#0-1) 

the router is `msg.sender` to the pool. The pool then passes the router address as `sender` through `ExtensionCalling._beforeSwap` to every configured extension.

`SwapAllowlistExtension.beforeSwap` then evaluates: [3](#0-2) 

Here `msg.sender` is the pool (correct namespace key) and `sender` is the **router address**, not the end user. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly, making the router the `msg.sender` seen by the pool.

**Two concrete failure modes:**

| Scenario | Outcome |
|---|---|
| Pool admin allowlists the router so users can trade | Every non-allowlisted user bypasses the per-user gate by routing through the router |
| Pool admin does not allowlist the router | Every allowlisted user is blocked from using the primary swap interface |

No existing guard compensates for this: the extension has no mechanism to decode the original initiator from `extensionData`, and the pool does not propagate the original `tx.origin` or any caller-chain context.

## Impact Explanation

Curated pools deploy `SwapAllowlistExtension` specifically to restrict which addresses may trade — KYC-gated pools, institutional pools, or pools with regulatory constraints. The bypass is complete: any address can call `MetricOmmSimpleRouter.exactInputSingle` and trade on a pool that was supposed to reject them. Unauthorized users can drain liquidity at oracle-quoted prices, and the pool admin has no on-chain mechanism to prevent it without also blocking all legitimate router users. This constitutes broken core pool functionality with direct fund-impacting consequences.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface deployed alongside the core pool. Any user following the standard integration path (router → pool) triggers the bypass automatically, without any special knowledge of the vulnerability. The trigger requires only a standard `exactInputSingle` call — no privileged access, no malicious setup, no non-standard tokens. The bypass is automatic and repeatable by any address.

## Recommendation

The extension must check the economically relevant actor — the end user — not the intermediary router.

**Option A (preferred):** Have the router encode the original `msg.sender` into `extensionData` and have the extension decode it. This requires a convention between router and extension but preserves the pool's stateless design.

**Option B:** Require direct pool interaction for allowlisted pools. Document and enforce (e.g., at extension `initialize`) that pools using `SwapAllowlistExtension` must not be used with the router. This is the simplest safe option if the router cannot be modified.

**Option C:** Check `tx.origin` as a fallback — this is generally discouraged but would correctly identify the end user in non-contract-wallet flows.

The root fix is that `SwapAllowlistExtension.beforeSwap` must not treat the direct pool caller as the gated identity when an intermediary router is a supported entry point.

## Proof of Concept

**Actors:** `poolAdmin` deploys pool with `SwapAllowlistExtension`, allowlists `alice` only. `bob` is not allowlisted. `router` is `MetricOmmSimpleRouter`.

**Step 1:** Pool admin allowlists the router so that `alice` can use it:
```solidity
extension.setAllowedToSwap(pool, address(router), true);
```

**Step 2:** `bob` (not allowlisted) calls the router:
```solidity
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    ...
}));
```

**Result:** Pool calls `_beforeSwap(msg.sender=router, ...)`. Extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds despite not being on the allowlist.

**Alternatively (no router allowlist):** Pool admin allowlists only `alice`:
```solidity
extension.setAllowedToSwap(pool, alice, true);
```
`alice` calls `router.exactInputSingle(...)`. Pool calls `_beforeSwap(msg.sender=router, ...)`. Extension checks `allowedSwapper[pool][router]` → `false`. Alice's swap reverts with `NotAllowedToSwap` even though she is explicitly allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
