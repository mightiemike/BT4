Audit Report

## Title
Swap Allowlist Bypassed via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` at the pool level. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore evaluates the router's allowlist status rather than the actual user's, rendering the allowlist completely ineffective for all router-mediated swaps.

## Finding Description
`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`, passing the immediate caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` encodes that `sender` value directly into the `IMetricOmmExtensions.beforeSwap` call dispatched to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` performs its allowlist check against this `sender`: [3](#0-2) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When `MetricOmmSimpleRouter.exactInputSingle()` calls `IMetricOmmPoolActions(params.pool).swap(...)`: [4](#0-3) 

...the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. The same applies to `exactInput()`, `exactOutputSingle()`, and `exactOutput()`: [5](#0-4) 

Two broken outcomes follow:
1. **Router is allowlisted** (required for any router-mediated swap to work): every user, allowlisted or not, bypasses the curated-pool gate by routing through the router.
2. **Router is not allowlisted**: no user can swap through the router on the curated pool, breaking the primary supported swap entry point.

The exact wrong value is `sender = router_address` where `sender = actual_user_address` is required for the allowlist invariant to hold.

## Impact Explanation
Any non-allowlisted user can trade on a pool whose operator deployed `SwapAllowlistExtension` to restrict access (e.g., KYC-gated, compliance-gated, or partner-only pools). The bypass is unconditional for all router-mediated swaps — the primary supported entry point. This constitutes broken core pool functionality and admin-boundary bypass: the pool admin's access-control invariant is violated by an unprivileged path, enabling unauthorized price impact, fee extraction, or LP-value leakage by actors the pool operator explicitly excluded.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entry point. Any user who calls `exactInputSingle` or `exactInput` triggers the bypass automatically. No special setup, flash loan, or privileged access is required. The bypass is unconditional and repeatable for every router-mediated swap on any allowlist-gated pool.

## Recommendation
The extension must check the identity of the economic actor, not the intermediary contract. Two options:

1. Have `MetricOmmPool.swap()` accept an explicit `originator` parameter that the router populates with `msg.sender` (the actual user), and pass that as `sender` to `_beforeSwap` instead of `msg.sender`.
2. Have the extension check a user-supplied field in `extensionData` that the router populates with the originating user address, combined with a router-authenticity check (e.g., only trust `extensionData`-supplied identity when `msg.sender` is a known factory-registered router).

The fix must ensure the checked identity is the same actor who economically benefits from the swap, regardless of which supported entry point is used.

## Proof of Concept

```
Setup:
  - Deploy MetricOmmPool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)   // router must be allowlisted for normal use
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker (non-allowlisted) calls MetricOmmSimpleRouter.exactInputSingle({
         tokenIn: token0, tokenOut: token1, pool: pool, ...
     })
  2. Router calls pool.swap(recipient=attacker, ...)
     → pool sees msg.sender = router
     → pool passes sender=router to _beforeSwap (MetricOmmPool.sol:231)
  3. ExtensionCalling._beforeSwap encodes sender=router into beforeSwap call (ExtensionCalling.sol:164)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] == true → PASSES (SwapAllowlistExtension.sol:37)
  5. Swap executes; attacker receives token1 output

Result:
  attacker successfully swaps on a pool they are not allowlisted for.
  The allowlist guard is bypassed for every router-mediated swap.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
