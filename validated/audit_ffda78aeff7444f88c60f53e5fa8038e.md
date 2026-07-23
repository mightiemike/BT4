Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is the pool's own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address, not the actual user. If the router is allowlisted, every non-allowlisted user can bypass the curated pool's swap gate by calling through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called the pool: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the pool's `msg.sender` the router address: [3](#0-2) 

The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants to enable router-based trading for allowlisted users will naturally allowlist the router address. Once the router is allowlisted, every user — including those explicitly excluded from the individual allowlist — can call `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` through the router and the extension passes, because it only sees the router address. There is no existing guard that checks the original caller identity; the `extensionData` field is passed through but the extension does not inspect it for a forwarded user identity.

## Impact Explanation
A curated pool's entire swap-access policy is defeated. Any non-allowlisted user can execute swaps against the pool by routing through `MetricOmmSimpleRouter`. LP funds are exposed to trades the pool admin explicitly intended to block, constituting a direct loss of the curation invariant and broken core pool functionality. This matches the allowed impact gate: broken core pool functionality causing loss of funds and unauthorized access to LP liquidity.

## Likelihood Explanation
The trigger is unprivileged: any user can call the public router. The precondition — the router being allowlisted — is a natural and expected admin action for any curated pool that also wants to support the standard periphery UX. No special setup or malicious initial configuration is required beyond the normal deployment of a curated pool with `SwapAllowlistExtension`.

## Recommendation
`SwapAllowlistExtension.beforeSwap` must gate on the economically relevant actor, not the intermediary. The correct fix is to require the router to forward the original caller identity in `extensionData`, and have the extension verify that field when `sender` is a known/trusted router. Alternatively, pool admins must be explicitly documented that allowlisting the router grants swap access to all users, and router-mediated swaps on allowlisted pools must be treated as unsupported unless the identity-forwarding mechanism is implemented.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin allowlists router: setAllowedToSwap(pool, router, true)
  - Admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes; attacker receives output tokens

Result:
  - attacker, who is not on the allowlist, successfully swaps against the curated pool
  - The allowlist invariant is broken; LP funds are exposed to unauthorized counterparties
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
