Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` sets to `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` is used, `sender` is the router's address, not the end-user's. If the pool admin allowlists the router (required for any allowlisted user to trade through it), every user — including those not individually allowlisted — can bypass the swap gate by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making itself `msg.sender` of that call. The original end-user's address is stored only in transient callback context (via `_setNextCallbackContext`) for token settlement and is never forwarded to the pool or the extension: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutput`, and `exactOutputSingle`. The bypass path is:
1. Admin deploys a pool with `SwapAllowlistExtension` as a `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, router, true)` so allowlisted users can trade through the router.
3. Non-allowlisted user `charlie` calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(...)` — pool sees `msg.sender = router`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true`.
6. `charlie`'s swap executes successfully despite never being individually allowlisted.

There is no existing guard that recovers the original caller: the `extensionData` field passed from the router to the pool is the user-supplied `params.extensionData` (line 79), not the router-injected caller address. [4](#0-3) 

## Impact Explanation
Any user can trade in a pool configured to restrict swaps to a curated set of addresses (KYC-gated, institutional-only, favorable oracle pricing for specific LPs). This completely defeats the purpose of `SwapAllowlistExtension` for router-mediated flows. Depending on the pool's purpose, unauthorized swaps can drain LP value or violate compliance requirements — a direct loss of LP principal or protocol-level policy failure. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entry point for end-users. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address — there is no other mechanism. This is a natural and expected administrative action, making the bypass reachable in any production deployment of a curated pool that supports router access. No special attacker capability is required beyond calling the public router.

## Recommendation
The extension must gate the economically relevant actor — the end-user — not the intermediary contract. Options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and `SwapAllowlistExtension.beforeSwap()` decodes and checks it when `sender` is a known trusted router. This requires a trusted router registry in the extension.

2. **Restrict the router to only allowlisted callers**: Add a pre-check in `MetricOmmSimpleRouter` that verifies `msg.sender` is allowlisted before forwarding to the pool. This keeps the extension logic unchanged but requires the router to be extension-aware.

3. **Extend the extension with a trusted-router registry**: When `sender` is a trusted router, require the actual payer (passed via `extensionData`) to be allowlisted instead of the router address.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, alice allowlisted, router allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // admin enables router

// charlie (NOT individually allowlisted) bypasses the gate via router
vm.prank(charlie);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: token0,
        tokenOut: token1,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: charlie,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Swap succeeds: extension saw sender=router (allowlisted), never checked charlie
```

The extension receives `sender = address(router)`, finds it in `allowedSwapper[pool][router]`, and returns the success selector — `charlie`'s swap settles in full. [5](#0-4)

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
