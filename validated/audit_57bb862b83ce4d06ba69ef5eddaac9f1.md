Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()` — the router contract address when a user routes through `MetricOmmSimpleRouter`. The extension checks `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actual_user]`. A pool admin who allowlists the router to enable router-mediated swaps for curated users inadvertently grants unrestricted swap access to every caller, completely defeating the allowlist invariant.

## Finding Description

**Exact call chain:**

`MetricOmmSimpleRouter.exactInputSingle` calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` at line 72, making the router the `msg.sender` to the pool.

`MetricOmmPool.swap` passes `msg.sender` (the router) as the first argument to `_beforeSwap` at lines 230–231:
```solidity
_beforeSwap(
    msg.sender,   // router address
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim as `sender` to the extension at lines 162–165:
```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then evaluates at line 37:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool (correct), sender = router (wrong actor)
```

The effective check is `allowedSwapper[pool][router]`. Once the pool admin calls `setAllowedToSwap(pool, router, true)` — the natural step to enable router-mediated swaps — every unprivileged user can bypass the allowlist by calling `router.exactInputSingle()` or `router.exactInput()`, since the router address is always the `sender` seen by the extension.

**Contrast with `DepositAllowlistExtension`:** `beforeAddLiquidity` explicitly ignores the first `sender` argument and gates on `owner` (the LP position owner), which is preserved end-to-end. The swap extension has no equivalent preserved "real actor" field — `sender` is structurally the router when routed.

**No existing guard prevents this:** The router's `_setNextCallbackContext` stores the real user as `payer` in transient storage for payment purposes only; this value is never forwarded to the extension or encoded into `extensionData`.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set (KYC'd counterparties, whitelisted institutions, protocol-controlled addresses) loses that restriction entirely for any caller routing through `MetricOmmSimpleRouter`. LP assets are exposed to unrestricted swaps at oracle-derived prices from any address, constituting a direct loss of LP principal and a complete failure of the pool's core access-control invariant. This matches the "Broken core pool functionality causing loss of funds" and "Admin-boundary break" impact categories.

## Likelihood Explanation

The only precondition is that the pool admin allowlists the router — a routine operational step for any pool intending to support the standard periphery. No privileged escalation, no malicious setup, and no non-standard tokens are required. Any unprivileged user who observes the allowlist state (public mapping) can immediately exploit it by calling the public router functions.

## Recommendation

Pass the original user through the swap path so the extension can gate on the economically relevant actor. The cleanest fix: the router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` decodes and checks it when `extensionData` is non-empty. The router already stores the real user in transient storage via `_setNextCallbackContext` (as `payer`); it should additionally encode it into `extensionData` passed to the pool. Alternatively, add a dedicated `originator` field to the swap call signature so the router can forward the real user alongside the pool's `msg.sender`.

## Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; router allowlisted by admin
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// attacker is NOT individually allowlisted

// Attacker bypasses allowlist via router
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:             address(pool),
        tokenIn:          address(token0),
        tokenOut:         address(token1),
        zeroForOne:       true,
        amountIn:         1_000,
        amountOutMinimum: 0,
        recipient:        attacker,
        deadline:         block.timestamp + 1,
        priceLimitX64:    0,
        extensionData:    ""
    })
);
// pool.swap() is called with msg.sender=router
// extension checks allowedSwapper[pool][router] == true → passes
// attacker swaps on curated pool despite never being individually allowlisted
```

**Relevant code:**
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3) 
- [5](#0-4)

### Citations

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
