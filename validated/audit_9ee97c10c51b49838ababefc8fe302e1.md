Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the end-user, allowing any unprivileged user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` captured inside `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][user]`. Any pool admin who allowlists the router to enable standard periphery UX inadvertently grants every caller of the router the ability to swap in the curated pool, fully defeating the per-user allowlist.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap(...)` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. Once the router is allowlisted, the check passes for every caller of the router regardless of whether they were individually allowlisted.

The `DepositAllowlistExtension` does **not** share this flaw: its `beforeAddLiquidity` gates the `owner` argument (the position owner), which is preserved correctly even when the liquidity adder is the `msg.sender`: [5](#0-4) 

## Impact Explanation
Any user can trade in a pool configured to restrict swaps to a curated set of addresses. The allowlist — the sole mechanism preventing unauthorized access — is fully bypassed. Unauthorized swaps drain LP positions and collect fees from liquidity deposited under the assumption that only vetted counterparties would trade against it. This constitutes a direct loss of LP principal and a broken core pool invariant, qualifying as High severity under Sherlock thresholds.

## Likelihood Explanation
The trigger condition is that the pool admin has allowlisted the router address. This is the expected and natural configuration for any curated pool that also wants to support the standard periphery UX. The admin has no indication that allowlisting the router opens the gate to all users; the allowlist UI and `AllowedToSwapSet` events give no warning. The exploit requires no privileged keys, no special tokens, and no flash loans — only a call to the public router. The condition is therefore highly likely to be met in production deployments.

## Recommendation
The extension must resolve the true end-user identity rather than trusting the `sender` argument when the caller is a known intermediary. Two sound approaches:

1. **Pass-through identity**: Require the router to forward the original `msg.sender` as an explicit parameter in `extensionData`, and have the extension decode and check that value instead of `sender` when `sender` is a known intermediary.
2. **Recipient-based check**: Gate on `recipient` (the second argument to `beforeSwap`) rather than `sender` when the pool is configured to use the router, since the recipient is the economic beneficiary of the swap.
3. **Router-aware allowlist**: The extension can maintain a separate registry of trusted intermediaries and, when `sender` is a known intermediary, require that the decoded end-user from `extensionData` is individually allowlisted.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-based swaps
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is not allowlisted

Attack:
  1. Alice calls MetricOmmSimpleRouter.exactInputSingle(pool, ..., recipient=alice, ...)
  2. Router calls pool.swap(recipient=alice, ...) with msg.sender=router
  3. Pool calls _beforeSwap(sender=router, recipient=alice, ...)
  4. Extension evaluates: allowedSwapper[pool][router] == true  → passes
  5. Swap executes; alice receives output tokens from the curated pool.

Expected: revert NotAllowedToSwap (alice is not allowlisted)
Actual:   swap succeeds because the router is allowlisted and sender == router

Foundry test outline:
  - Deploy SwapAllowlistExtension and pool with beforeSwap hook.
  - Deploy MetricOmmSimpleRouter.
  - swapExtension.setAllowedToSwap(pool, address(router), true);
  - Assert alice (not allowlisted) can call router.exactInputSingle and swap succeeds.
  - Assert alice calling pool.swap directly reverts with NotAllowedToSwap.
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
