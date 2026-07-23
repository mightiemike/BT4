Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Allowing Any User to Bypass Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the direct caller of the pool. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router's address, not the actual user. If the pool admin allowlists the router (the only way to enable router-based swaps under this design), every user — including those not individually allowlisted — can bypass the curated swap gate by calling through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the pool see `msg.sender = router`: [4](#0-3) 

The actual end-user's address is never inspected. Once the router is allowlisted, `allowedSwapper[pool][router]` passes for every call arriving through the router, regardless of who the real user is. `MetricOmmSimpleRouter` is a public, permissionless contract, so any address can call it and satisfy the allowlist check.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` checks the `owner` argument — the explicit position owner passed through the call chain — which is not collapsed to the router's address: [5](#0-4) 

The existing integration test `test_allowedSwapSucceeds` allowlists `callers[0]` (the direct intermediary) and never tests the case where a second, non-allowlisted user routes through the same allowlisted intermediary, leaving the bypass undetected: [6](#0-5) 

## Impact Explanation
Any user can trade on a pool whose admin intended to restrict swaps to a curated set of addresses. The allowlist provides no protection once the router is allowlisted. LP funds are exposed to unrestricted trading by actors the pool admin explicitly excluded — arbitrageurs, sanctioned addresses, or competitors depending on the pool's purpose. This is a direct bypass of a core access-control invariant, constituting a broken core pool functionality and an admin-boundary break reachable by an unprivileged path.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented periphery entry point. A pool admin who deploys a curated pool and wants allowlisted users to be able to use the router must allowlist the router address — this is the only mechanism available under the current design. The bypass is therefore triggered by normal, expected admin configuration, not an edge-case misconfiguration. Any user who discovers the router is allowlisted can exploit it immediately with a single transaction.

## Recommendation
The `SwapAllowlistExtension` must gate the economic actor, not the direct caller. The simplest safe fix is to document that the router must never be allowlisted and that all swaps on allowlisted pools must go directly to the pool. If router support is required, the extension must be redesigned: `MetricOmmSimpleRouter` should encode the real user's address into `extensionData`, and `SwapAllowlistExtension.beforeSwap` should decode and verify that address when `sender` is a known router, rather than checking `sender` directly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Admin calls swapExtension.setAllowedToSwap(pool, router, true)
    (allowlisting the router so that allowlisted users can swap through it).
  - Alice (allowlisted individually) and Bob (not allowlisted) both exist.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
  2. Router calls pool.swap(recipient=Bob, ...).
  3. Pool calls _beforeSwap(sender=router, ...).
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true.
  5. Swap executes. Bob receives output tokens.

Result:
  Bob, who was never individually allowlisted, successfully swaps on the
  curated pool. The allowlist is completely bypassed.
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
