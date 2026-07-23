Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Originating User, Allowing Any Caller to Bypass Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is `msg.sender` of the `pool.swap()` call — the router, not the originating user. When a pool admin allowlists the router to permit allowlisted users to swap via `MetricOmmSimpleRouter`, every unprivileged user gains the same access by routing through the router, fully defeating the allowlist.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the direct caller) as the first argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as `sender` to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router — not the originating user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly; the original `msg.sender` is stored only in the callback context for payment purposes and is never forwarded to the pool as an observable identity for extensions: [4](#0-3) 

The result is a forced impossible choice for the pool admin: if the router is not allowlisted, allowlisted users cannot use the router at all; if the router is allowlisted, every non-allowlisted user bypasses the restriction by routing through it. No configuration simultaneously enforces the allowlist and permits router-mediated swaps.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to KYC'd or curated addresses is fully bypassed. Any unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutput`) targeting the restricted pool. The extension sees the router as the swapper, passes the check if the router is allowlisted, and the user receives output tokens they were never permitted to receive — violating the pool's access-control invariant and potentially draining liquidity reserved for curated participants. This constitutes a broken core pool functionality causing loss of funds and an admin-boundary break where an unprivileged path bypasses a configured restriction.

## Likelihood Explanation
The router is the standard, documented entry point for swaps. Any pool admin who wants allowlisted users to use the router must add the router to the allowlist, which immediately opens the bypass to all users. The attack requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call to a restricted pool.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Document incompatibility and revert on router calls**: Until a proper user-forwarding mechanism exists, `SwapAllowlistExtension` should revert if `sender` is a known router address, or document that it is incompatible with router-mediated swaps.

## Proof of Concept
```solidity
// Pool deployed with SwapAllowlistExtension.
// Admin allowlists alice (KYC'd) and the router (so alice can use it).
// Bob (not allowlisted) bypasses:
router.exactInputSingle(ExactInputSingleParams({
    pool:             restrictedPool,
    recipient:        bob,
    zeroForOne:       true,
    amountIn:         1e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    extensionData:    ""
}));
// pool.swap() called with msg.sender = router
// _beforeSwap(sender = router, ...)
// allowedSwapper[pool][router] == true → passes
// Bob receives token1 output despite never being allowlisted
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
