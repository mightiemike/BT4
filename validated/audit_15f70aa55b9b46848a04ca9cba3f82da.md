Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any Caller to Bypass the Per-User Allowlist via the Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the router contract is `msg.sender` of `pool.swap()`, not the originating user. A pool admin who allowlists the router — the only way to let curated users trade through the standard periphery — simultaneously opens the pool to every address on-chain, completely defeating the per-user access gate.

## Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the entity that calls `pool.swap()`: [3](#0-2) 

So the allowlist check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The same structural problem exists for multi-hop `exactInput` (intermediate hops use `address(this)` as payer, but the pool still sees the router as `msg.sender`): [4](#0-3) 

And for `exactOutput`, the recursive callback path also calls `pool.swap()` from the router context: [5](#0-4) 

There is no mechanism in the extension, pool, or router to recover the originating user's identity. The `allowAllSwappers` escape hatch exists but is a separate flag; the per-user `allowedSwapper` mapping is the broken control. No existing guard compensates for the identity mismatch introduced by the router intermediary.

## Impact Explanation
A restricted pool using `SwapAllowlistExtension` is designed to limit swapping to a curated set of counterparties (e.g., institutional participants, KYC'd addresses). If the router is allowlisted — which is the only way to let those curated users trade through the standard periphery — any unpermissioned address can route through `MetricOmmSimpleRouter` and execute swaps against the pool's LP positions. This exposes LP capital to adversarial flow from actors the pool admin explicitly intended to exclude, constituting a direct loss path for LP principal. This matches the "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" allowed impact criteria.

## Likelihood Explanation
The trigger requires the pool admin to have allowlisted the router. This is the natural and expected configuration for any pool that wants its allowlisted users to access the standard periphery. The admin has no other option: either allowlist the router (opening the bypass) or leave it un-allowlisted (breaking router access for legitimate users). The bypass is therefore reachable in every realistic deployment of a router-accessible allowlisted pool. The attack requires zero privileged access from the attacker — any EOA can call `exactInputSingle`.

## Recommendation
The extension must gate on the originating user, not the immediate caller of `pool.swap()`. The most robust fix is to have the router inject `msg.sender` (the originating user) into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and verify that address against the allowlist instead of using the raw `sender` parameter. This requires a coordinated router+extension design. Alternatively, remove the router from the allowlist model entirely and rely solely on `extensionData`-carried identity. Checking `recipient` instead of `sender` is not reliable for multi-hop flows where intermediate recipients are the router itself.

## Proof of Concept
```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true   // alice is the only permitted swapper
  allowedSwapper[pool][router] = true  // admin adds router so alice can use it

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: bob, ...})

  router calls:
    pool.swap(bob, zeroForOne, amount, priceLimit, "", extensionData)
    // msg.sender of pool.swap() = router

  pool calls _beforeSwap(sender=router, ...)
  extension checks: allowedSwapper[pool][router] == true  ✓ passes

  bob's swap executes against LP positions — allowlist fully bypassed.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, set `allowedSwapper[pool][alice] = true` and `allowedSwapper[pool][router] = true`, call `router.exactInputSingle` from `bob` (not allowlisted), assert the swap succeeds and `bob` receives output tokens.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-181)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );
```
