Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, allowing full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When end users route through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so `sender` equals the router address. A pool admin who allowlists the router to enable standard UX for permitted users inadvertently grants swap access to every caller, completely defeating the per-user allowlist.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, where `msg.sender` is whoever called `pool.swap()`. [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`**, where `msg.sender` is the pool (the extension caller) and `sender` is the value forwarded above — i.e., the router address when routed through `MetricOmmSimpleRouter`: [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly**, making the router the `msg.sender` seen by the pool. The original end-user `msg.sender` is stored only in transient callback context for payment purposes and is never forwarded to the pool as the swap initiator: [4](#0-3) 

The same substitution occurs in `exactInput` (all hops), `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

**The structural trap:** A pool admin who wants allowlisted users (e.g., alice) to access the pool via the standard router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, so the check passes for every caller regardless of their identity. The per-user allowlist is completely bypassed for any swap routed through `MetricOmmSimpleRouter`. [6](#0-5) 

## Impact Explanation

Any address can swap against a pool that the admin intended to restrict to a specific set of counterparties, simply by calling `MetricOmmSimpleRouter`. If the pool offers favorable oracle-anchored pricing (e.g., a private market-making arrangement), unauthorized traders can extract value from LP positions. The `SwapAllowlistExtension` is the only on-chain mechanism for per-user swap gating; its bypass removes the sole access-control layer protecting LP funds in restricted pools. This constitutes a direct loss of LP principal through unauthorized extraction of favorable pricing, meeting the Critical/High threshold for broken core pool functionality causing loss of funds.

## Likelihood Explanation

The router is the canonical, documented swap entry point for end users. A pool admin who deploys a restricted pool and then wants allowlisted users to use the standard UX will naturally allowlist the router address — this is a foreseeable operational step, not an exotic misconfiguration. The bypass requires no special privilege: any EOA or contract can call `MetricOmmSimpleRouter.exactInputSingle` with the restricted pool address. The attack is repeatable and requires no setup beyond the admin's own legitimate configuration action.

## Recommendation

Pass the economically relevant actor — the end user — rather than the immediate `msg.sender` of `pool.swap`. Two concrete options:

1. **Router-side**: Have `MetricOmmSimpleRouter` pass the original `msg.sender` as a dedicated `swapper` field in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify it against the allowlist. The extension should reject calls where `extensionData` is empty or malformed when the pool has an active allowlist.

2. **Protocol-level**: Extend the swap interface to include an explicit `swapper` address parameter that the pool forwards to extensions as `sender`, with the router responsible for injecting its `msg.sender` into that field.

Either approach must ensure the checked identity cannot be spoofed by the caller supplying arbitrary `extensionData`.

## Proof of Concept

```
Setup
─────
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)  // needed so alice can use the router

Attack
──────
4. attacker (not in allowlist) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool:       restrictedPool,
       recipient:  attacker,
       zeroForOne: true,
       amountIn:   X,
       ...
     })

5. Router calls pool.swap(attacker, true, X, ...) — msg.sender = router
6. Pool calls _beforeSwap(router, attacker, ...)
7. Extension checks allowedSwapper[pool][router] == true  ✓
8. Swap executes; attacker receives output tokens.

Result: attacker bypasses the per-user allowlist and swaps against a pool
        that was supposed to be restricted to alice only.

Foundry test outline:
- Deploy SwapAllowlistExtension, pool, router
- Admin: setAllowedToSwap(pool, alice, true); setAllowedToSwap(pool, router, true)
- vm.prank(attacker); router.exactInputSingle({pool: pool, ...})
- Assert swap succeeds (no NotAllowedToSwap revert)
- Assert attacker received output tokens
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
