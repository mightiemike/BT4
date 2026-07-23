Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Allowlist Bypass via MetricOmmSimpleRouter - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user who calls through the router, completely defeating the allowlist.

## Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which is `msg.sender` of the original `MetricOmmPool.swap()` call: [1](#0-0) 

`_beforeSwap` then passes `sender` directly into the extension call: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly, making `msg.sender = router` from the pool's perspective: [3](#0-2) 

The extension therefore sees `sender = router address`, not the actual user. The allowlist check becomes `allowedSwapper[pool][router]`. Once the router is allowlisted, every user who calls through the router passes the check regardless of individual authorization.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the position owner explicitly passed by the caller), not `sender` (the payer/intermediary): [4](#0-3) 

The deposit extension is immune to this issue because the `owner` identity is preserved through the liquidity adder path.

## Impact Explanation

A curated pool (e.g., KYC-only, institutional-only, or partner-restricted) that uses `SwapAllowlistExtension` and allowlists the router to support standard periphery access is fully open to any user who calls through the router. The allowlist provides zero protection on the router path. Unauthorized traders can execute swaps against the pool's oracle-anchored liquidity, exposing LPs to trades they explicitly intended to restrict. In oracle-anchored pools, LP funds are at risk from unauthorized arbitrageurs or adversarial traders who should have been blocked. This constitutes a broken core pool access control mechanism causing potential loss of funds to LPs.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery swap entrypoint. Any pool admin who wants to allow router-mediated swaps (the normal user flow) must allowlist the router. The bypass is reachable through the standard supported periphery path with no special setup beyond the natural admin configuration. The precondition — router being allowlisted — is the expected operational state for any pool that supports periphery access.

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the actual end-user identity, not the direct caller of `swap()`. Two options:

1. **Pass the real user via `extensionData`**: Require the router to encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires router cooperation and extension-data validation.
2. **Document that the router must never be allowlisted**: Require allowlisted users to call the pool directly. This is a design constraint, not a code fix, and is fragile.

The cleaner fix is option 1: the extension should decode the actual user from `extensionData` when `sender` is a known router, or the pool should expose a mechanism for the router to attest the original caller.

## Proof of Concept

```
Setup:
- Deploy pool with SwapAllowlistExtension configured
- Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
- Pool admin does NOT allowlist attacker EOA

Attack:
- attacker (non-allowlisted EOA) calls router.exactInputSingle({pool: pool, ...})
- router calls pool.swap(recipient, zeroForOne, ...) with msg.sender = router
- pool calls extension.beforeSwap(sender=router, ...)
- extension checks allowedSwapper[pool][router] → true → PASSES
- attacker's swap executes against the curated pool

Expected: revert NotAllowedToSwap
Actual: swap succeeds
```

The `SwapAllowlistExtension` check at line 37 evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][attacker]`, so the allowlist is bypassed for every user who routes through the router. [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
