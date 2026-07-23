Audit Report

## Title
SwapAllowlistExtension checks router address instead of actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, the router is `msg.sender`, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. Any unprivileged user can bypass per-user swap restrictions by routing through the router whenever the router address is allowlisted.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that `sender` value unchanged into every registered extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router contract `msg.sender` as seen by the pool: [4](#0-3) 

The router stores the real initiating user only in transient storage for callback settlement and never forwards that address to the pool or any extension. The extension therefore evaluates `allowedSwapper[pool][router]` — a single boolean covering every user who routes through the router — rather than the individual user's entry. This creates two mutually exclusive failure modes:

1. **Allowlist bypass (primary impact):** If the pool admin allowlists the router address to support router-based swaps, every user — including those not individually allowlisted — can bypass the per-user gate by calling the router instead of the pool directly.
2. **Allowlisted users blocked:** If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all, breaking the standard swap interface for legitimate participants.

## Impact Explanation
Any unprivileged user can swap on a pool intended to be restricted to specific counterparties by calling `MetricOmmSimpleRouter` instead of the pool directly. This defeats the allowlist's purpose and allows unauthorized parties to interact with restricted pools — extracting value from LP positions, violating compliance requirements, or trading against pricing intended only for vetted counterparties. LP principal is at direct risk if the pool's economics assume a controlled set of swap counterparties. This constitutes broken core pool functionality causing potential loss of funds, matching the allowed impact gate.

## Likelihood Explanation
High. The router is the standard, expected user-facing entry point for swaps. A pool admin who wants to support router-based swaps for allowlisted users has no choice but to allowlist the router address, which immediately opens the pool to all users. No special privileges, unusual tokens, or off-chain coordination are required — any user can call the router with arbitrary parameters.

## Recommendation
The extension must recover the actual initiating user's identity. Two viable approaches:

1. **`extensionData` attestation:** Require the router to encode the real initiating user's address in `extensionData` and verify it (with a signature or trusted-forwarder pattern) inside the extension.
2. **Direct-call-only policy:** Document and enforce that pools using `SwapAllowlistExtension` must not allowlist the router and must require users to call the pool directly. Add an extension-level flag or factory-level check that rejects calls whose `sender` is a known router address.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured as a `beforeSwap` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice, and `setAllowedToSwap(pool, router, true)` to allow router-based swaps for Alice.
3. Bob (not individually allowlisted) calls `router.exactInputSingle({pool: pool, tokenIn: ..., amountIn: ..., ...})`.
4. The router calls `pool.swap(recipient, zeroForOne, amountIn, priceLimit, "", extensionData)` with `msg.sender = router`.
5. The pool calls `_beforeSwap(router, ...)` → `SwapAllowlistExtension.beforeSwap(router, ...)`.
6. The extension evaluates `allowedSwapper[pool][router]` = `true` → no revert.
7. Bob's swap executes successfully on a pool he was never individually authorized to use.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
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
