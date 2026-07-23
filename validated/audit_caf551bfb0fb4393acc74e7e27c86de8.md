Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of the `pool.swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract address, not the end user. A pool admin who allowlists the router to enable standard periphery access inadvertently grants swap access to every user of that router, completely defeating the per-user access control the extension was designed to enforce.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (used as the mapping namespace) and `sender` is the direct caller of `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`. The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

No existing guard resolves the originating user identity. The `extensionData` field is passed through but the extension does not decode it for user identity. There is no `tx.origin` check or router-attested user forwarding mechanism.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists `MetricOmmSimpleRouter` (the natural step to enable standard periphery access) inadvertently grants swap access to every address. Any non-allowlisted user can call `router.exactInputSingle` and the extension passes because `allowedSwapper[pool][router] == true`. LP providers on curated pools — e.g., those restricted to KYC'd counterparties or specific market makers — are exposed to trades from arbitrary actors they explicitly excluded. This is a direct policy bypass with fund-impacting consequences: unauthorized counterparties trade against restricted pool liquidity. [6](#0-5) 

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants users to interact through the standard periphery must allowlist the router. The misconfiguration is the expected, natural configuration for any pool intended to be accessible via the periphery. There is no NatSpec warning in `SwapAllowlistExtension` that allowlisting any intermediary contract collapses per-user granularity. [7](#0-6) 

## Recommendation

The extension must resolve the end user identity, not the direct pool caller. The most practical correct fix is to have the router encode the originating user (`msg.sender` at router entry) into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify that value when the direct `sender` is a known trusted router. Alternatively, document `SwapAllowlistExtension` as only suitable for pools where users call the pool directly (no router intermediary), and build a separate extension that decodes user identity from router-attested `extensionData`. At minimum, the NatSpec must warn that allowlisting any intermediary contract grants that contract's entire user base access to the pool.

## Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Admin calls setAllowedToSwap(pool, router, true)   // allow standard periphery
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker (not allowlisted) calls:
      router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) — msg.sender at pool = router
  - pool calls _beforeSwap(sender=router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] == true  ✓
  - Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker trades on a curated pool they were explicitly excluded from
  - LP providers are exposed to unauthorized counterparties
  - The allowlist invariant is broken for all router-mediated swaps
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-15)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}
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
