Audit Report

## Title
SwapAllowlistExtension Allowlist Bypassed via Router: Any User Can Swap in Restricted Pools When Router Is Allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, the pool receives `sender = router`, not the originating user. If the pool admin allowlists the router address — the necessary step to let approved users trade through the standard periphery — every caller of the router bypasses the per-user allowlist check, enabling unauthorized traders to swap against the restricted pool and drain LP reserves.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

So `sender` arriving at the extension is the **router address**, not the originating user. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (the only way to let approved users trade through the standard periphery), the check passes unconditionally for every caller of the router. The same flaw applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. No existing guard in the extension, pool, or router prevents this substitution.

## Impact Explanation
A pool deploying `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers). Once the router is allowlisted, the restriction is nullified: any address can call `MetricOmmSimpleRouter` and execute swaps against the pool. Unauthorized traders can drain LP reserves at oracle-quoted prices, extract value from bins, and pay fees that were intended only for the restricted participant set. This constitutes direct loss of LP principal — a Critical/High impact under Sherlock thresholds.

## Likelihood Explanation
The bypass requires the pool admin to have allowlisted the router. This is the expected operational step for any pool that wants its approved users to trade through the standard periphery rather than calling the pool directly. There is no documentation warning against it, and the `setAllowedToSwap` / `setAllowAllSwappers` API gives no indication that allowlisting the router collapses the per-user gate. Any unprivileged user can then trigger the bypass with a single `exactInputSingle` call — no special setup, no flash loan, no privileged role required.

## Recommendation
The extension must gate on the originating user, not the intermediary. The cleanest fix is to have the router encode `msg.sender` into `extensionData`, and have the extension decode and verify it. This requires a trusted router convention: the extension checks that `msg.sender` (the pool) is the expected pool, and that the decoded user is allowlisted. Alternatively, a dedicated router wrapper that re-checks the allowlist before forwarding to the pool can enforce the invariant at the periphery layer, so the pool-level extension only needs to trust the wrapper.

## Proof of Concept
```
Setup
─────
1. Pool admin deploys a pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is approved
3. Pool admin calls setAllowedToSwap(pool, router, true)  // router allowlisted so alice can use periphery

Attack
──────
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool:      restrictedPool,
           recipient: bob,
           ...
       })

5. Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → pool.swap: msg.sender = router → sender = router
   → _beforeSwap(sender=router, ...)
   → SwapAllowlistExtension.beforeSwap:
         allowedSwapper[pool][router] == true  ✓  (passes)

6. Swap executes. Bob receives tokens from the restricted pool.
   Alice's LP position is drained at oracle price by an unauthorized counterparty.
```

Foundry test outline:
1. Deploy `SwapAllowlistExtension`, pool, and `MetricOmmSimpleRouter`.
2. Call `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, router, true)`.
3. As Bob (not allowlisted), call `router.exactInputSingle(...)` targeting the restricted pool.
4. Assert the swap succeeds and Bob receives output tokens, demonstrating the bypass.