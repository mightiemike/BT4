Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter passed from the pool, which is the pool's `msg.sender` — the direct caller of `pool.swap`. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (required for any router-mediated swap to work), every address on the network can bypass the per-pool swap allowlist by routing through the router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value as the first positional argument to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the pool's `msg.sender` the router, not the end user: [4](#0-3) 

The router stores the real payer in transient storage via `_setNextCallbackContext` for callback settlement, but never surfaces that identity to the pool or to any extension: [5](#0-4) 

This creates an irreconcilable conflict: if the router is not allowlisted, allowlisted users cannot swap through the router at all. If the router is allowlisted, every address can bypass the allowlist by routing through the router. There is no in-protocol mechanism to distinguish the real user behind the router.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. Because the extension sees the router address instead of the real trader, any non-allowlisted address can trade freely in the curated pool by using the standard router interface. This constitutes a complete admin-boundary break: unauthorized users can interact with the pool in ways the admin explicitly intended to prevent, including draining liquidity at oracle-quoted prices and extracting value from LP positions. This meets the contest threshold for a High severity finding under the "Admin-boundary break" and "Broken core pool functionality" impact categories.

## Likelihood Explanation
The router is the canonical, documented swap interface for end users. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. The moment they do, the bypass is open to every address with no special privileges. The exploit requires no special setup beyond calling `MetricOmmSimpleRouter.exactInputSingle` with the target pool — a standard user action. The bypass is repeatable and unconditional.

## Recommendation
The pool should forward the original caller's identity to extensions through a dedicated field rather than reusing `msg.sender`. One concrete approach: add an `originator` parameter to `IMetricOmmExtensions.beforeSwap` that the pool populates with `msg.sender` on direct calls and that the router populates (via `extensionData` or a separate field) with the real user address. `SwapAllowlistExtension` should then gate on `originator`, not on `sender`. Alternatively, the router can embed the real user address in `extensionData` and the extension can decode and verify it, provided the pool enforces that the embedded address matches the callback payer stored in transient storage.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // only alice is allowed
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for router-mediated swaps
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) — pool's msg.sender = router
6. Pool calls _beforeSwap(sender=router, ...)
7. Extension checks: allowedSwapper[pool][router] == true  → passes
8. Bob's swap executes successfully despite not being on the allowlist.
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
