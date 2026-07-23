Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender`—the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual end user. If the router is allowlisted on a curated pool (the necessary operational step to support router-based swaps), every user—including non-allowlisted ones—can bypass the per-user access control by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside `pool.swap()`: [4](#0-3) 

The router does not encode the real end user into `extensionData`—it passes `params.extensionData` directly from the caller's input: [5](#0-4) 

The same pattern applies to `exactInput()`, `exactOutputSingle()`, and `exactOutput()`: [6](#0-5) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who wants to support router-based swaps on a curated pool must allowlist the router address. Once the router is allowlisted, the check passes for every call arriving through the router, regardless of who the actual end user is. The per-user allowlist is completely defeated.

## Impact Explanation
Any non-allowlisted user can trade on a curated pool configured to restrict access to specific counterparties (e.g., KYC-gated, institutional-only, or whitelist-only pools). The attacker receives real token output from the pool; the pool's LPs bear economic exposure to an actor the pool admin explicitly excluded. This is a direct, complete failure of the access-control guarantee that LPs and the pool admin relied upon when deploying a curated pool. This constitutes broken core pool functionality causing loss of the curation guarantee and falls under the "Admin-boundary break" allowed impact category.

## Likelihood Explanation
The trigger requires only that the pool admin has allowlisted the router—a natural and necessary operational step for any curated pool that wants to support the standard periphery. No privileged access, no special tokens, and no multi-step setup is needed by the attacker. A single `exactInputSingle` call through the publicly deployed, permissionless router suffices. The condition is met by default for any curated pool that supports router-based swaps.

## Recommendation
The extension must gate on the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes `msg.sender` into `extensionData` for each hop, and the extension decodes and checks it. This requires a coordinated change to the router and the extension.
2. **Maintain a trusted-router registry in the extension**: When `sender` is a known trusted router, require the real user to be encoded in `extensionData` and check that instead. When `sender` is not a known router, check `sender` directly as today.

Either way, the extension must not treat the router address as the identity to gate.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is not allowlisted.
4. Bob (non-allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
6. The pool calls `extension.beforeSwap(router, recipient, ...)`.
7. The extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. Bob's swap executes successfully, bypassing the per-user allowlist entirely.

A Foundry integration test can confirm this by: deploying the extension and pool, setting allowances as above, calling `exactInputSingle` from an address not in the allowlist, and asserting the swap succeeds rather than reverting with `NotAllowedToSwap`.

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
