Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the originating user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router (required for approved users to use the router) inadvertently grants every on-chain user the ability to bypass the per-user restriction.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool via `CallExtension.callExtension`) and `sender` is the first argument forwarded from the pool. The pool always passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged into the ABI-encoded call:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
``` [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput` / `exactOutputSingle` / `exactOutput`) calls `pool.swap(...)`, the pool's `msg.sender` is the **router**, not the originating user:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
``` [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` — the router's allowlist entry — rather than the actual user's entry. This creates an irresolvable dilemma for pool admins:

| Router allowlisted? | Allowlisted user via router | Non-allowlisted user via router |
|---|---|---|
| No | **Blocked** (broken UX) | Blocked |
| Yes | Passes | **Passes (bypass)** |

There is no configuration that simultaneously allows approved users to use the router and blocks unapproved users from using it.

## Impact Explanation
Any user can bypass a `SwapAllowlistExtension`-protected pool by routing through `MetricOmmSimpleRouter`. Pools intended for KYC'd, institutional, or whitelisted counterparties are open to the general public. Unrestricted users can execute swaps against oracle-anchored liquidity provisioned only for specific counterparties, draining LP positions at prices the LPs did not intend to offer to arbitrary actors. This constitutes broken core pool functionality causing direct loss of LP funds, matching the allowed impact gate for "Broken core pool functionality causing loss of funds" and "Admin-boundary break bypassed by an unprivileged path."

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who configures `SwapAllowlistExtension` and wants approved users to be able to use the router **must** allowlist the router address — this is the natural, expected configuration. No special knowledge or privileged access is required; any user can call `router.exactInputSingle` with a valid pool address. The bypass is structural and reproducible on every router-mediated swap.

## Recommendation
**Short term**: `SwapAllowlistExtension` should check the originating user rather than the direct caller of `pool.swap()`. The router already stores the originating `msg.sender` in transient storage via `_setNextCallbackContext`. One approach: pass the originating user as part of `extensionData` so the extension can read the true initiator. Alternatively, the pool could expose a separate "originator" field in the hook arguments.

**Long term**: Audit all extensions that gate by `sender` and verify that the `sender` binding is semantically correct for every supported periphery entry point (router single-hop, router multi-hop, liquidity adder). Establish a protocol-level convention distinguishing the "economic actor" (the user who initiated the action) from the "direct caller" (the contract that called the pool).

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, zeroForOne, amount, ...)` — pool's `msg.sender` = router.
6. Pool calls `_beforeSwap(router, bob, ...)` → extension receives `sender = router`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Swap executes. Bob receives output tokens from a pool he was never authorized to access.

Bob never needed to be on the allowlist. The guard is fully bypassed on the canonical periphery path.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L162-176)
```text
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
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
