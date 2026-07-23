Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's immediate `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to support router-mediated swaps for legitimate users inadvertently grants swap access to every user, breaking the core access-control invariant of the extension.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the check at line 37:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by `ExtensionCalling._beforeSwap`, which receives it directly from the pool's own `msg.sender`. [1](#0-0) [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the pool's `msg.sender` the router contract address, not the originating user: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap(...)` with the router as `msg.sender`. [4](#0-3) [5](#0-4) 

This creates an irreconcilable dilemma: if the admin does not allowlist the router, allowlisted users cannot use the router at all. If the admin does allowlist the router (the expected operational action), `allowedSwapper[pool][router] == true` passes the guard for every caller regardless of their individual allowlist status. The extension's implicit assumption that `sender == actual user` is violated by any intermediary contract.

`DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position beneficiary passed explicitly as a separate argument), not `sender` (the pool's `msg.sender`). [6](#0-5) 

## Impact Explanation

A pool admin who configures a curated pool with `SwapAllowlistExtension` for KYC compliance, institutional-only access, or regulatory restrictions, and also allowlists the router to support router-mediated swaps for legitimate users, inadvertently opens the pool to all users. Any non-allowlisted user bypasses the allowlist by calling `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) targeting the curated pool. This constitutes an admin-boundary break: the pool admin's access-control configuration is bypassed by an unprivileged path, exposing LP funds in a curated pool to trades from actors the pool was explicitly designed to exclude.

## Likelihood Explanation

The trigger requires the pool admin to have allowlisted the router address — a reasonable and expected operational action for any curated pool that also wants to support router-mediated swaps for its allowlisted users. Once the router is allowlisted, any unprivileged user can exploit the bypass with a standard router call. No special permissions, flash loans, or multi-step setup are required. The exploit is repeatable indefinitely.

## Recommendation

The `SwapAllowlistExtension` must check the actual originating user, not the pool's immediate `msg.sender`. Two viable approaches:

1. **Extension-data forwarding**: The router forwards the actual user's address in `extensionData`; the extension decodes and checks that address. The pool admin must trust the router to forward honestly, so this requires the router to be a verified, non-upgradeable contract.
2. **Pool-level effective-swapper field**: The pool passes a separate "effective swapper" argument (e.g., derived from a router-signed payload) to the extension, distinct from the immediate `msg.sender`.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as before-swap hook.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // allowlist Alice (KYC'd)
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist router for UX

Attack (Bob, not allowlisted):
  4. Bob calls MetricOmmSimpleRouter.exactInputSingle({
         pool: curatedPool,
         recipient: bob,
         zeroForOne: true,
         amountIn: 1000,
         ...
     });

Execution trace:
  router.exactInputSingle()                          // msg.sender = Bob
    → pool.swap(recipient=bob, ...)                  // pool.msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (no revert)
      → swap executes for Bob

Result: Bob's swap succeeds despite not being on the allowlist.
```

The extension checks `allowedSwapper[pool][router]` (true) instead of `allowedSwapper[pool][bob]` (false), silently passing the guard. [7](#0-6) [3](#0-2)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-137)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
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
