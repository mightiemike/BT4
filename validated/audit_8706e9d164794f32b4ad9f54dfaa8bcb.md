Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Allowlist Bypass or Blocking Allowlisted Users - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. This creates a structural mismatch: either the router is allowlisted (any unprivileged user bypasses the allowlist) or it is not (every allowlisted user is blocked from using the router). The `extensionData` field is available but entirely ignored by the extension, leaving no workaround.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making itself `msg.sender` of that call — the router address, not the user: [4](#0-3) 

The same pattern holds for `exactInput` (all hops) and `exactOutputSingle`: [5](#0-4) [6](#0-5) 

The `bytes calldata` parameter in `beforeSwap` is unnamed and unused — there is no mechanism to recover the original caller's identity from `extensionData`. This produces two mutually exclusive failure modes:

**Mode A — Allowlist bypass:** Admin allowlists the router to support router-mediated swaps. Because `sender = router` for every router-originated call, `allowedSwapper[pool][router] == true` passes for every caller regardless of whether they are on the intended allowlist. Any unprivileged user routes through `MetricOmmSimpleRouter` and trades on the curated pool.

**Mode B — Allowlisted users blocked:** Admin allowlists specific user addresses but not the router. Allowlisted users who call through the router have `sender = router`, which is not in `allowedSwapper`, so their swaps revert with `NotAllowedToSwap`. The primary supported periphery path is permanently unusable for every allowlisted user.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant actor) rather than `sender` (the liquidity adder contract), demonstrating the correct pattern exists in the codebase: [7](#0-6) 

## Impact Explanation

**Mode A** is a direct admin-boundary break: an unprivileged user bypasses a pool's curated allowlist by routing through the protocol's own supported periphery. Depending on pool design, this allows unauthorized parties to drain LP-favorable pricing, violate compliance-gated pools, or access positions the allowlist was meant to protect. **Mode B** constitutes broken core pool functionality: the primary supported swap path (`MetricOmmSimpleRouter`) is permanently unusable for every allowlisted user on any pool using this extension, forcing direct pool interaction. Both impacts fall within the allowed impact gate (admin-boundary break; broken core swap functionality).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Pool admins deploying a `SwapAllowlistExtension` pool who want to support router-mediated swaps for their allowlisted users will naturally add the router to `allowedSwapper`, immediately triggering Mode A. Admins who do not add the router will immediately observe that their allowlisted users cannot swap via the router (Mode B). Both failure modes are reachable through normal, expected usage of the protocol's own periphery with no special privileges or malicious setup required.

## Recommendation

`beforeSwap` should check the economically relevant actor — the address that initiated the trade — rather than the immediate caller of `pool.swap`. Two complementary fixes:

1. **Pass the original caller through `extensionData`:** The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; `SwapAllowlistExtension` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct calls.
2. **Require routers to attest the real user:** Define a standard extension-data envelope that trusted routers populate with the real user address, and have `SwapAllowlistExtension` verify it — mirroring how `DepositAllowlistExtension` checks `owner` rather than `sender`.

## Proof of Concept

**Mode A (allowlist bypass):**
1. Deploy a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Attacker (address not in `allowedSwapper`) calls `MetricOmmSimpleRouter.exactInputSingle` targeting the pool.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(router, ...)`.
6. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
7. Attacker's swap executes on the curated pool despite never being allowlisted.

**Mode B (allowlisted users blocked):**
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` but does **not** allowlist the router.
2. Alice calls `MetricOmmSimpleRouter.exactInputSingle`.
3. Router calls `pool.swap(...)` with `msg.sender = router`.
4. `SwapAllowlistExtension` checks `allowedSwapper[pool][router] == false` → reverts `NotAllowedToSwap`.
5. Alice, despite being allowlisted, cannot use the router.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-137)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
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
