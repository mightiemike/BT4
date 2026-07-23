Audit Report

## Title
SwapAllowlistExtension Gates on Router Address Instead of Originating User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist, but `MetricOmmPool.swap` passes its own `msg.sender` as `sender`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the originating user. A pool admin who allowlists the router to permit their curated users to trade through the supported periphery simultaneously grants unrestricted access to every address on the network, nullifying the curation policy entirely.

## Finding Description

`MetricOmmPool.swap` captures `msg.sender` and forwards it verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates that value against the per-pool allowlist mapping, where `msg.sender` is the pool and `sender` is the direct pool caller: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call. The originating user's address is stored only in transient storage for the payment callback and is never forwarded to the pool or extension: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore evaluates `allowedSwapper[pool][router]` for every swap arriving through the router, regardless of who the originating user is. The original user's address is never seen by the guard.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants their allowlisted users to trade through the supported periphery must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the guard passes for every call arriving through the router regardless of who initiated it. Any address on the network can call `MetricOmmSimpleRouter.exactInputSingle` targeting the curated pool and the extension will approve the swap because `allowedSwapper[pool][router] == true`. The entire curation policy is nullified — users who were explicitly blocked can trade freely, and the pool admin has no mechanism to distinguish them at the extension layer. This constitutes a broken core pool functionality (the access-control extension is rendered inoperative) and an admin-boundary break where an unprivileged path bypasses the pool admin's intended access control.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary supported swap interface for end users. Any pool admin who configures a `SwapAllowlistExtension` and also wants their allowlisted users to use the router — a natural and expected operational requirement — will trigger this condition. The router is a public, permissionless contract, so no privileged access is required. The attacker only needs to call a standard router function with the target pool address. The precondition (router being allowlisted) is the expected operational state, not an edge case.

## Recommendation

Pass the originating user through the call chain so the extension can gate on the economically relevant actor. The simplest correct fix is to have the router store `msg.sender` in transient storage before calling the pool and expose a `getSwapOriginator()` view that the extension can call back to retrieve the true user. Alternatively, add an `originator` field to the swap `extensionData` and have the router sign or attest it, or require pool admins to rely on a separate per-user check inside the extension (e.g., via signed permits in `extensionData`) rather than allowlisting the router address directly. The current design where `sender` always equals the direct pool caller must be documented prominently if not fixed, as it makes `SwapAllowlistExtension` incompatible with any router-based flow.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
  - Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack:
  - bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
        pool: curated_pool,
        tokenIn: token0,
        recipient: bob,
        amountIn: X,
        ...
    })
  - Router calls pool.swap(bob_recipient, ...) with msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes; bob receives output tokens from the curated pool
  - alice's exclusive access policy is violated with zero privileged setup
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
