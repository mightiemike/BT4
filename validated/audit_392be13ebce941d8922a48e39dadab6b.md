Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Full Allowlist Bypass via Router — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the router is allowlisted rather than whether the user is allowlisted. Any user who calls the router on a pool where the router address is allowlisted bypasses the curated access control entirely.

## Finding Description

The full call chain is confirmed in production code:

**Step 1:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router `msg.sender` to the pool: [1](#0-0) 

**Step 2:** `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

**Step 3:** `ExtensionCalling._beforeSwap` forwards that same `sender` value (the router) to the extension: [3](#0-2) 

**Step 4:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — never the end user: [4](#0-3) 

This creates an impossible choice for pool admins: allowlisting the router grants every user on earth the ability to swap through it, defeating the allowlist entirely. Not allowlisting the router means individually allowlisted users cannot use the router at all.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` argument (the position owner explicitly passed by the caller), not the immediate caller `sender`: [5](#0-4) 

## Impact Explanation

A curated pool relying on `SwapAllowlistExtension` to restrict trading to approved counterparties (e.g., KYC'd or protocol-internal users) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The unauthorized swapper can drain LP-owned token reserves at oracle-quoted prices, causing direct loss of LP principal. The allowlist — the only on-chain mechanism preventing this — provides zero protection once the router is allowlisted. This constitutes a direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation

The router is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to have normal UX (deadline, slippage, multi-hop) will allowlist the router. The moment they do, the allowlist is void. The attacker requires no special privilege, no flash loan, and no oracle manipulation — a single call to `exactInputSingle` suffices. The condition is not hypothetical; it is the expected operational configuration.

## Recommendation

Pass the originating user through the call chain rather than the immediate caller. Two concrete options:

1. **Router forwards the real sender**: Add a `sender` field to the swap parameters that the router populates with `msg.sender` and the pool passes to extensions instead of its own `msg.sender`.
2. **Extension reads from transient storage**: The router writes the real user into a transient slot before calling the pool; the extension reads it. This mirrors the pattern already used for the callback payer in `MetricOmmSwapRouterBase`.

Either way, `SwapAllowlistExtension.beforeSwap` must compare against the end user's address, not the address of whatever contract called `pool.swap`.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][router] = true   // admin allowlists router so users can swap
  allowedSwapper[pool][alice]  = true   // alice is individually approved
  allowedSwapper[pool][bob]    = false  // bob is NOT approved

Attack:
  bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → pool.swap(msg.sender = router)
    → _beforeSwap(sender = router)
    → SwapAllowlistExtension.beforeSwap(sender = router)
    → allowedSwapper[pool][router] == true  → passes
    → bob's swap executes, draining LP reserves

Result:
  bob, a disallowed swapper, successfully trades on a curated pool.
  The allowlist provided zero protection.
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
