Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Originating User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which equals `msg.sender` of the pool's `swap` call. When users route through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router — a natural operational step to enable router-mediated swaps for approved users — simultaneously grants swap access to every unprivileged address on the network, fully bypassing the allowlist.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value verbatim to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool (`msg.sender` = pool, `sender` = pool's caller): [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The same pattern holds for `exactInput` (L104), `exactOutputSingle` (L136), and `exactOutput` (L165) — in every case the pool sees `msg.sender = router`: [5](#0-4) 

The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originating_user]`. The pool admin faces an impossible choice: do not allowlist the router (allowlisted users cannot use it) or allowlist the router (every user bypasses the allowlist). No configuration achieves "only allowlisted users may swap via the router."

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the position owner, not the caller), making it router-transparent: [6](#0-5) 

`SwapAllowlistExtension` has no equivalent identity-preserving design.

## Impact Explanation

When the pool admin allowlists the router to enable router-mediated swaps for approved users, every unprivileged address can call `exactInputSingle` (or any other router entry point) and have their swap accepted by the extension. The allowlist — the sole access-control layer on the swap path — is rendered inoperative. Unauthorized counterparties can drain LP assets, execute arbitrage against restricted pools, or interact with pools designed for KYC'd or institutional participants only. This constitutes direct loss of LP principal and a broken core pool invariant (only approved swappers may trade), meeting the "Broken core pool functionality causing loss of funds" and "Critical/High direct loss of user principal" impact criteria.

## Likelihood Explanation

Medium. The pool admin must explicitly allowlist the router. However, this is a natural and expected operational step: allowlisted users will attempt to use the standard router, their swaps will revert (router not allowlisted), the admin will allowlist the router to fix it — unknowingly opening the gate to all users. The mistake is easy to make, hard to detect, and `isAllowedToSwap` returns `true` for the router without revealing that this implies universal access.

## Recommendation

Pass the originating user's address to the extension rather than the immediate caller of `pool.swap`. Two concrete options:

1. **Extension-data forwarding**: The router encodes `msg.sender` into `extensionData`; `SwapAllowlistExtension` decodes and checks it. The pool admin must also configure the extension to trust the router as a forwarder.
2. **Dedicated `originalSender` field**: Add an `originalSender` parameter to the `beforeSwap` hook signature that the pool populates from a transient-storage context set by the router (analogous to how the router already stores payer context for callbacks via `_setNextCallbackContext`).

Until fixed, pool admins must be explicitly warned that allowlisting the router is equivalent to setting `allowAllSwappers = true`.

## Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension.
2. Pool admin calls setAllowedToSwap(pool, alice, true)    // Alice is the intended user.
3. Alice tries exactInputSingle via router → reverts (router not allowlisted).
4. Pool admin calls setAllowedToSwap(pool, router, true)   // "fix" for Alice.
5. Attacker (Charlie, never allowlisted) calls:
       router.exactInputSingle({pool: pool, ...})
6. Pool calls swap(); msg.sender = router.
7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
8. Charlie's swap executes against LP assets; allowlist fully bypassed.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
