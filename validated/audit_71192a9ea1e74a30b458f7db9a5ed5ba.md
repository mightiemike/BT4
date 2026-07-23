Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of Real Swapper, Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. This creates two broken configurations: if the router is allowlisted, every non-allowlisted user bypasses the curated-pool gate; if the router is not allowlisted, every individually allowlisted user is silently blocked from using the only supported periphery path.

## Finding Description
**Root cause — three-contract chain:**

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged into the encoded extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on `sender` (the router) rather than the real user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` with itself as `msg.sender`; the real user appears only as `params.recipient`: [4](#0-3) 

The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**Why existing guards fail:** The extension has no mechanism to distinguish a router-mediated call from a direct call. `allowedSwapper[pool][sender]` is the sole check; no fallback reads `recipient` or decodes `extensionData` for an original-caller field. [6](#0-5) 

## Impact Explanation
**Scenario A — Complete allowlist bypass (High):** A pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists the router so that legitimate users can swap through the standard periphery path (`allowedSwapper[pool][router] = true`). Because the extension checks the router address, every non-allowlisted user can call `exactInputSingle` through the router and pass the guard. The curated-pool invariant is fully broken; unauthorized users trade against LP funds at oracle prices, causing direct LP loss on a pool designed to be restricted. This is a broken core pool functionality / direct loss of LP principal impact.

**Scenario B — Allowlisted users locked out (Medium/High):** If the admin does not allowlist the router, every individually allowlisted user is silently blocked when calling any router function, because the extension sees the router address and reverts `NotAllowedToSwap`. The only usable path is a raw `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` — an interface unavailable to EOAs. Core swap functionality is broken for the intended user set.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. Any pool that deploys `SwapAllowlistExtension` and expects users to interact through the router will encounter this bug on the first router-mediated swap. The trigger requires no special privileges — any public caller can reach it. The pool admin's only "safe" configuration (not allowlisting the router) locks out all allowlisted users from the router, making the extension effectively unusable with the periphery layer.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should gate on the real economic actor, not the intermediary. The cleanest fix is to have the router encode the real user address (`msg.sender`) into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension` decode and verify that address, with a fallback to `sender` when no override is present. Alternatively, add a trusted-router registry to the extension so that when `sender` is a known router, the extension decodes the original caller from `extensionData`.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can swap through it
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Alice is NOT on the allowlist
// allowedSwapper[pool][alice] = false

// Alice calls the router — extension sees router as sender, passes the check
router.exactInputSingle(ExactInputSingleParams({
    pool:             address(pool),
    recipient:        alice,
    zeroForOne:       true,
    amountIn:         1_000e18,
    amountOutMinimum: 0,
    priceLimitX64:    0,
    deadline:         block.timestamp,
    tokenIn:          token0,
    extensionData:    ""
}));
// ✓ swap succeeds — Alice bypassed the allowlist
// Extension received sender = address(router), allowedSwapper[pool][router] = true → guard passes
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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
