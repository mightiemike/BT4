Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual swapper, allowing allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the actual user. A pool admin who allowlists the router to enable router-based swaps for their approved users inadvertently opens the gate to every user who calls through the router, breaking the per-user allowlist invariant entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

When the call path is `user → MetricOmmSimpleRouter → pool.swap()`, `msg.sender` inside the pool is the router address.

`SwapAllowlistExtension.beforeSwap` then evaluates: [2](#0-1) 

Here `msg.sender` is the pool and `sender` is the router. The check passes if `allowedSwapper[pool][router]` is `true`.

`MetricOmmSimpleRouter.exactInputSingle` stores the actual caller only in transient storage for the payment callback and does not forward it to the pool or extension: [3](#0-2) 

The actual user's address (`msg.sender`) is captured by `_setNextCallbackContext` for payment purposes only and is never visible to `beforeSwap`. The extension has no mechanism to recover the real caller's identity.

The allowlist mapping is keyed per-pool per-swapper: [4](#0-3) 

When the pool admin adds the router with `setAllowedToSwap(pool, router, true)`, the single entry satisfies the check for every caller who routes through the router, regardless of individual allowlist status.

## Impact Explanation
Any non-allowlisted user can swap in a pool configured with `SwapAllowlistExtension` by calling `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle`. Pools designed to be KYC-gated, institutional-only, or restricted to specific counterparties are fully open to unrestricted arbitrage and toxic flow. LP positions in such pools suffer direct loss of principal because the allowlist invariant — "only approved addresses may swap" — is broken for every pool that allowlists the router. This matches the allowed impact: broken core pool functionality causing loss of funds and admin-boundary break via an unprivileged path.

## Likelihood Explanation
Medium. The bypass requires the pool admin to add the router to the allowlist, which is a predictable and natural operational step: an admin who wants their approved users to use the standard router will add the router address. The mistake is non-obvious because the admin expects the router to act as a transparent forwarder of user identity. Once the router is allowlisted, any EOA can exploit the bypass with a single call to the router — no special privilege, no front-running, and the condition persists indefinitely.

## Recommendation
The extension must check the economically relevant actor, not the intermediary. Two concrete options:

1. **Forward real caller via `extensionData`**: Have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before calling `pool.swap`. In `beforeSwap`, if `sender` is a known/trusted router, decode the actual user from `extensionData` and apply `allowedSwapper[pool][decodedUser]` instead.
2. **Router-aware fallback in the extension**: Maintain a registry of trusted routers in `SwapAllowlistExtension`. If `sender` is a registered router, require `extensionData` to carry the real caller's address (signed or encoded by the router) and check that address against the allowlist.

Either approach closes the gap between the configured gate (individual users) and the identity actually checked (router address).

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` in the `beforeSwap` slot.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(params.recipient, ...)` — inside the pool, `msg.sender` = router.
6. Pool calls `_beforeSwap(router, ...)`. Extension evaluates `allowedSwapper[pool][router]` → `true` → passes.
7. Bob's swap executes in the restricted pool. The per-user allowlist is fully bypassed.

Foundry test outline: deploy pool with extension, call `setAllowedToSwap` for Alice and the router, impersonate Bob (unlisted), call `exactInputSingle` through the router, assert the swap succeeds and Bob receives output tokens.

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
