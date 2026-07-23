Audit Report

## Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper on every `MetricOmmSimpleRouter`-mediated swap — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`. When any swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router, not the end user. `SwapAllowlistExtension.beforeSwap` therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, making the allowlist gate either fully bypassable (if the router is allowlisted) or permanently broken for router users (if it is not).

## Finding Description
`MetricOmmPool.swap` at L230-231 passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` receives that value as `sender` and gates on it: [2](#0-1) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap`. When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap`, the pool's `msg.sender` is the router: [3](#0-2) 

The same applies to `exactInput` (L103-112), `exactOutputSingle` (L135-137), `exactOutput` (L165-181), and the recursive callback path `_exactOutputIterateCallback` (L220-228): [4](#0-3) 

In every case the actual end-user address is never visible to the extension. No existing guard in the pool or router injects the real originator into the `sender` slot.

## Impact Explanation
Two concrete fund-impacting failure modes:

**Bypass (high impact):** Admin allowlists the router as a trusted periphery contract (`setAllowedToSwap(pool, router, true)`). Any non-allowlisted user calls `router.exactInputSingle(...)`. The extension sees `sender = router`, finds it allowlisted, and permits the swap. The curation gate is fully defeated; the pool accepts trades from arbitrary counterparties the admin intended to exclude. This directly breaks the allowlist invariant and constitutes broken core pool functionality.

**Lockout (medium impact):** Admin allowlists individual users but not the router. Those allowlisted users who call the router are blocked because the extension sees `sender = router` and finds it not allowlisted. The router — the protocol's primary swap interface — is unusable for any allowlisted pool, breaking core swap functionality for legitimate users.

## Likelihood Explanation
Allowlisting the router is a natural and expected admin action for a trusted periphery contract. The bypass is reachable through a normal, non-malicious admin configuration. The lockout requires no admin mistake at all — it triggers automatically the moment any allowlisted user attempts to use the router with an allowlist-gated pool. Both failure modes are repeatable and require no special attacker capability beyond calling a public router function.

## Recommendation
The pool's `swap` function should accept an explicit `originator` parameter that the router sets to `msg.sender` (the actual user) before calling the pool. `SwapAllowlistExtension.beforeSwap` would then gate on `originator` rather than the pool's `msg.sender`. Alternatively, the router can encode the real user address in `extensionData` and the extension can decode it, though this requires coordinated changes to both contracts. At minimum, `SwapAllowlistExtension` documentation must state it is incompatible with router-mediated swaps and that allowlisting the router opens the pool to all users.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls extension.setAllowedToSwap(pool, alice, true)   // alice is the intended grantee
3. Admin calls extension.setAllowedToSwap(pool, router, true)  // router added as trusted periphery
4. Attacker (bob, not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams{
           pool: pool, tokenIn: ..., tokenOut: ..., recipient: bob, ...
       })
5. pool.swap is called with msg.sender = router.
6. _beforeSwap(router, ...) is forwarded to SwapAllowlistExtension.beforeSwap with sender = router.
7. allowedSwapper[pool][router] == true → check passes.
8. Bob's swap executes; allowlist is bypassed.

Lockout variant:
3b. Admin does NOT allowlist the router (only alice).
4b. Alice calls router.exactInputSingle(...).
5b. pool.swap called with msg.sender = router.
6b. allowedSwapper[pool][router] == false → revert NotAllowedToSwap().
    Alice cannot use the router despite being individually allowlisted.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
