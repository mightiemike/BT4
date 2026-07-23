All cited code paths are confirmed in the repository. The claim is technically accurate across every step:

1. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the pool's `msg.sender` at swap time. [1](#0-0) 

2. `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [2](#0-1) 

3. `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` stores the real user only in transient callback context for payment; it never passes the user's address to `pool.swap`. [4](#0-3) 

The identity collapse is real and complete: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Allowlisting the router is the only mechanism available to a pool admin who wants to support router-mediated swaps, and doing so unconditionally grants every user bypass capability. The same collapse applies to multi-hop `exactInput` (intermediate hops use `address(this)` = router as `msg.sender`) and `exactOutput` recursive callback hops. [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension` Resolves Swapper Identity to Router Address, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` at the time `swap` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently grants every unprivileged user the ability to bypass the individual allowlist, fully breaking the access-control guarantee the extension is deployed to enforce.

## Finding Description
In `SwapAllowlistExtension.beforeSwap` (L37–39), the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` is the pool (correct); `sender` is the first parameter, which `MetricOmmPool.swap` sets to its own `msg.sender` (L231). `ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension (L149–177).

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` (L72–80). The pool's `msg.sender` is therefore the router. The actual user's address is stored only in transient callback context for payment (L71: `_setNextCallbackContext(..., msg.sender, ...)`), and is never passed to `pool.swap` or any extension.

The allowlist check resolves to `allowedSwapper[pool][router]`. A pool admin who allowlists the router to support router-mediated swaps for their curated users makes this check pass for **every caller** of the router, regardless of whether that caller is individually allowlisted. There is no mechanism in the extension or the router to recover the originating user's address and forward it to the extension. The same collapse applies to all hops in `exactInput` (L103–112) and `exactOutput` recursive callback hops (L220–228).

## Impact Explanation
Any user can bypass the swap allowlist on a curated pool by routing through `MetricOmmSimpleRouter`. For KYC-gated, institutional-only, or otherwise access-controlled pools, this allows unauthorized users to execute swaps, directly breaking the core security guarantee the `SwapAllowlistExtension` is deployed to enforce. This constitutes broken core pool functionality causing unauthorized access to restricted swap flows, meeting the allowed impact gate for a High-severity finding.

## Likelihood Explanation
A pool admin deploying `SwapAllowlistExtension` to gate individual users will naturally also want those users to be able to use the supported periphery router for multi-hop or slippage-protected swaps. Allowlisting the router is the only available mechanism to enable this. The admin is likely to do so without realizing the extension checks the router's address rather than the actual user's address, because the parameter name `sender` implies it carries the originating user identity. `MetricOmmSimpleRouter` is a public, permissionless contract callable by anyone.

## Recommendation
The `SwapAllowlistExtension` must check the economically relevant actor — the user who initiated the swap — not the intermediate contract that called `pool.swap`. Two viable approaches:

1. **Pass the real user through `extensionData`**: The router encodes the originating `msg.sender` into `extensionData` for each hop. The extension decodes and checks this address, falling back to `sender` when `extensionData` is empty (direct pool calls). This requires a convention between the router and the extension.

2. **Document and warn**: If the current design is intentional, the extension must clearly document that allowlisting the router grants access to all users, and pool admins must be warned never to allowlist a public router on a curated pool.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as the `beforeSwap` hook.
2. Pool admin allowlists the router to support router-mediated swaps: `swapExtension.setAllowedToSwap(pool, address(router), true)`.
3. An unprivileged user (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. The router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
5. `_beforeSwap(router, ...)` is dispatched; the extension evaluates `allowedSwapper[pool][router]` → `true`.
6. The swap executes successfully. The individual user allowlist is fully bypassed.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
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
