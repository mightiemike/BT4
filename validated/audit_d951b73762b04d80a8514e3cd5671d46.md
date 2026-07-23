Audit Report

## Title
`SwapAllowlistExtension` allowlist bypassed via router intermediary — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which equals `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool receives the router address as `sender`, not the end-user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users simultaneously opens the pool to every caller of the router, breaking the allowlist invariant entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool sees `sender = router`, not the end-user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Once `setAllowedToSwap(pool, router, true)` is called, `allowedSwapper[pool][router]` is `true` and the check passes for every caller of the router with no further discrimination: [6](#0-5) 

There is no mechanism in the extension to recover the original end-user identity from behind the router.

## Impact Explanation
Any address individually excluded from the allowlist can execute swaps on a restricted pool by routing through `MetricOmmSimpleRouter`. LP assets are exposed to swappers the pool admin explicitly intended to exclude. Depending on the restriction rationale (KYC gate, exploit-response lockdown, controlled bootstrapping), this constitutes a direct loss of LP principal or owed assets above Sherlock thresholds — a High severity impact.

## Likelihood Explanation
The trigger is a pool admin calling `setAllowedToSwap(pool, router, true)`, which is the natural and expected configuration step for any pool that wants its allowlisted users to be able to use the standard periphery router. The admin receives no warning from NatDoc or on-chain checks that this simultaneously opens the pool to all router callers. The bypass is then reachable by any unprivileged address with no further preconditions, making it highly likely to be triggered in production.

## Recommendation
The extension must verify the true end-user identity rather than trusting the `sender` argument forwarded by the pool. Two viable approaches:

1. **Authenticated pass-through**: Require the router to encode the original `msg.sender` inside `extensionData` with a verifiable signature or trusted-forwarder pattern, and have the extension decode and verify it.
2. **Block intermediary allowlisting**: Add an on-chain check in `setAllowedToSwap` that rejects known periphery contracts (router, multicall), and document that allowlisting any intermediary defeats the allowlist.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  admin calls setAllowedToSwap(pool, alice, true)    // alice is permitted
  admin calls setAllowedToSwap(pool, router, true)   // router added so alice can use it
  bob is NOT in allowedSwapper[pool]

Attack:
  bob calls router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

  router calls pool.swap(bob_recipient, true, X, ...)
    → pool calls _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension checks allowedSwapper[pool][router] → true
    → swap executes, bob receives output tokens

Result:
  bob, individually blocked, successfully swaps on the restricted pool.
  The allowlist invariant is broken; LP assets are exposed to an unauthorized swapper.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-20)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
