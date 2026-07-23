Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router (required for KYC'd users to use the standard periphery) inadvertently grants every user — including non-allowlisted ones — the ability to bypass the curated pool's swap gate.

## Finding Description
`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes this `sender` verbatim and dispatches it to each configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the first argument — the router address when the call originates from `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`. The actual end user (`msg.sender` of the router call) is stored only in transient storage as the payer via `_setNextCallbackContext` and is never forwarded to the pool or the extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The existing guard in `SwapAllowlistExtension` is insufficient because it checks the intermediary (router) rather than the actual trader. There is no mechanism in the current code to propagate the real end-user identity from the router to the extension.

## Impact Explanation
A pool admin deploying a curated pool with `SwapAllowlistExtension` faces an impossible choice: do not allowlist the router (KYC'd users cannot use the standard periphery at all), or allowlist the router (the allowlist check becomes `allowedSwapper[pool][router] == true` for every caller, granting unrestricted swap access to all users). The result is a complete bypass of the swap allowlist for any pool that permits router-mediated swaps. Non-allowlisted users can trade on pools intended to be restricted (e.g., institutional-only, KYC-gated, or regulatory-compliant pools), directly violating the pool's curation policy. This constitutes an admin-boundary break and broken core pool functionality.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary production swap entrypoint. Any pool admin who wants allowlisted users to use the standard router must allowlist the router address — this is the expected operational configuration. The bypass is reachable by any unprivileged user with no special setup: the attacker only needs to call `exactInputSingle` or `exactInput` on the router pointing at the curated pool.

## Recommendation
`SwapAllowlistExtension` must gate on the actual end user, not the intermediary. The cleanest fix is to have the router encode the real `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that address when the caller is a known router, falling back to `sender` for direct calls. Alternatively, a registry of trusted routers could be maintained in the extension, and when `sender` is a known router, the extension decodes the real user from `extensionData`. A simpler but less flexible approach is to check `tx.origin`, which is incompatible with smart-contract callers but would work for EOA-only pools.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is KYC'd
  3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
  4. bob is NOT allowlisted.

Attack:
  5. bob calls MetricOmmSimpleRouter.exactInputSingle({
       pool: curatedPool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     });

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...) [msg.sender = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓ (passes)
      → swap executes, bob receives output tokens

Result: bob successfully swaps on the curated pool despite not being individually allowlisted.
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
