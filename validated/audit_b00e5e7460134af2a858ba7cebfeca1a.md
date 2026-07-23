Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any Trader to Bypass Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call — the router, not the end user. Any pool admin who allowlists the router (required for allowlisted users to use it) simultaneously grants every unprivileged user the ability to bypass the restriction by routing through `MetricOmmSimpleRouter`. There is no configuration that permits allowlisted users to use the router while blocking non-allowlisted users.

## Finding Description

**Root cause — confirmed call chain:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router, not the original user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original `msg.sender` to the extension: [4](#0-3) 

**Exploit flow:**

1. Pool is deployed with `SwapAllowlistExtension`; admin allowlists `alice` (KYC'd) and the router (so `alice` can use it).
2. `bob` (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, ...})`.
3. Inside `pool.swap()`, `_beforeSwap(msg.sender=router, ...)` is called.
4. Extension checks `allowedSwapper[pool][router]` → `true` → passes.
5. `bob` receives output tokens despite never being allowlisted.

**Existing guards are insufficient:** The only guard is `allowedSwapper[msg.sender][sender]` at L37. There is no secondary check on `extensionData` or any other field that could identify the true end user. The `allowAllSwappers` escape hatch only makes the problem worse (open to all). [5](#0-4) 

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd, institutional, or otherwise curated addresses is fully bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, or `exactOutputSingle` targeting the restricted pool and receive output tokens they were never permitted to receive. This violates the pool's access-control invariant and constitutes broken core pool functionality — the allowlist extension provides zero protection when the router is in use, which is the standard, documented swap entry point.

## Likelihood Explanation

The router is the standard entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must add the router to the allowlist, which immediately opens the bypass to all users. The attack requires no special privileges, no flash loans, no unusual token behavior — only a standard router call. The precondition (router allowlisted) is the natural operational state for any pool that intends to support router-mediated swaps.

## Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Document incompatibility and revert on router calls**: Until a proper user-forwarding mechanism is in place, `SwapAllowlistExtension` should revert if `sender` is a known router address, making the incompatibility explicit and safe-by-default.

## Proof of Concept

```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists alice (KYC'd) and the router (so alice can use it).
// Bob (not allowlisted) bypasses the restriction:

router.exactInputSingle(ExactInputSingleParams({
    pool:             restrictedPool,
    recipient:        bob,
    zeroForOne:       true,
    amountIn:         1e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    tokenIn:          token0,
    extensionData:    ""
}));

// Inside pool.swap():
//   _beforeSwap(msg.sender = router, ...)
//   Extension checks: allowedSwapper[pool][router] == true  ← passes
//   Bob receives token1 output despite never being allowlisted.
```

A Foundry integration test can confirm this by: (1) deploying a pool with `SwapAllowlistExtension`, (2) allowlisting only `alice` and the router, (3) calling `router.exactInputSingle` as `bob`, and (4) asserting the call succeeds and `bob` receives output tokens.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
