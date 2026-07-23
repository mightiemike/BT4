Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`. When `MetricOmmSimpleRouter` is the caller, the extension receives the router's address as `sender` and checks it against the allowlist. Any unprivileged user can route through the public router to bypass a pool's swap allowlist entirely, provided the router is allowlisted — which is a prerequisite for any router-mediated swap to succeed.

## Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the first argument: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` value against `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` the pool observes: [3](#0-2) 

The same pattern applies to `exactInput` (L99–112), `exactOutputSingle` (L130–147), and `exactOutput` (L154–188). In all cases, the router is `msg.sender` to the pool. [4](#0-3) 

The forced choice is: allowlist the router (every user bypasses the guard) or don't (allowlisted users cannot use the router). There is no safe middle ground.

`DepositAllowlistExtension.beforeAddLiquidity` demonstrates the correct pattern: it checks `owner` (the position owner explicitly supplied by the caller) rather than `sender` (the direct caller), avoiding the same problem on the liquidity path: [5](#0-4) 

No equivalent identity-forwarding mechanism exists on the swap path.

## Impact Explanation

A pool deploying `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise allowlisted counterparties is fully defeated once the router is allowlisted. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the extension sees the router's address, passes the check, and the swap executes. Unauthorized swaps against a restricted pool can drain LP principal and protocol fees — a direct loss of user funds meeting Sherlock critical/high thresholds.

## Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. No special role, token balance, or prior interaction is required. Any user who can observe that the router is allowlisted on a restricted pool can exploit this in a single transaction. Likelihood is **High**.

## Recommendation

The extension must gate the actual end-user identity, not the intermediary. Viable approaches:

1. **Forwarded-sender via `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData`. The extension decodes and checks that address. Pool admins allowlist end users, not the router. Requires a convention between router and extension.
2. **Recipient-based check**: Gate on `recipient` instead of `sender` when the pool's intent is to restrict who receives output tokens. `recipient` is already available in the `beforeSwap` signature.
3. **Incompatibility documentation + enforcement**: Document that pools using `SwapAllowlistExtension` must only be accessed via direct `pool.swap()` calls, and add an on-chain guard that rejects known router addresses as `sender`.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // required for router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ..., extensionData: ""})
  - Router calls pool.swap(recipient, ...) — msg.sender to pool = router
  - Pool calls _beforeSwap(msg.sender=router, ...)
  - Extension checks allowedSwapper[pool][router] → true → PASSES
  - Swap executes; attacker receives output tokens

Result:
  - Attacker bypassed the allowlist entirely
  - allowedSwapper[pool][attacker] was never set to true
  - The guard checked the router, not the attacker
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
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
