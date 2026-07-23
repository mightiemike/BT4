Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any Caller to Bypass Per-Pool Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument against `allowedSwapper[pool][sender]`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so `sender` forwarded to the extension is the router address — not the end-user. Any non-allowlisted user can bypass the curated-pool gate by calling the public router, completely defeating the per-user access control.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as `sender` to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the allowlist keyed by `msg.sender` (the pool): [3](#0-2) 

When a user calls any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on `MetricOmmSimpleRouter`, the router calls `IMetricOmmPoolActions(pool).swap(...)` directly: [4](#0-3) 

At that point the pool sees `msg.sender = router`, so `sender` forwarded to the extension is the router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. If the pool admin allowlists the router to support normal UX, every user — including those explicitly excluded — can swap freely by routing through the router. No existing guard in the extension, pool, or router checks the originating user identity.

## Impact Explanation
A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set (e.g., KYC'd counterparties). Once the router is allowlisted to support normal operation, the allowlist provides zero protection: any address can call `MetricOmmSimpleRouter` and execute swaps against the pool. Unauthorized users can drain LP liquidity at oracle prices and extract spread/notional fees from positions never meant to be exposed to them. This is a direct loss of LP principal and a broken core pool invariant (curated access). Severity: High — direct loss of user/LP principal with no privilege required.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing entry point deployed alongside the protocol. Any pool that (a) deploys `SwapAllowlistExtension` and (b) wants to support router-mediated swaps — the normal operational mode — must allowlist the router, triggering the bypass. The attacker needs no special privilege: a single public call to any of the four router swap functions suffices. The condition is reachable on every production curated pool that uses the router.

## Recommendation
The extension must gate the economically relevant actor — the end-user — not the intermediary. Two complementary fixes:

1. **Pass the original initiator through the router**: `MetricOmmSimpleRouter` should encode `msg.sender` as a verified `swapper` field in `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and check that field when `sender` (the pool's caller) is a known router address.

2. **Add a verified `swapper` parameter to the swap call path**: Pool's `swap()` could accept an explicit `swapper` address that the router populates with its own `msg.sender` before calling the pool, matching the deposit-side design where `owner` (the position beneficiary) is passed explicitly and checked separately from `sender` (the payer).

## Proof of Concept
1. Pool is deployed with `SwapAllowlistExtension` configured. Only `alice` is allowlisted: `allowedSwapper[pool][alice] = true`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for `alice`.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` — pool sees `msg.sender = router`.
5. `_beforeSwap(router, ...)` is dispatched; extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. `bob` successfully swaps on a pool he was explicitly excluded from, receiving output tokens at oracle price — a direct loss of LP assets to an unauthorized counterparty.

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
