Audit Report

## Title
Swap Allowlist Checks Router Address Instead of End-User, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. A pool admin who allowlists the router — the necessary step to let curated-pool users access periphery — simultaneously opens the gate to every unprivileged caller, completely defeating the allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool: [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The allowlist therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`. Once the admin allowlists the router address, every caller — including those not on the allowlist — passes the check.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks the `owner` argument, which is the economically relevant depositor: [4](#0-3) 

The swap extension has no equivalent correction. The original end-user identity is not threaded through `extensionData` or any other channel, so there is no existing guard that can recover it.

## Impact Explanation

Any user not on the swap allowlist can execute swaps on a curated pool by routing through `MetricOmmSimpleRouter`. This breaks the core access-control invariant of allowlisted pools: unauthorized actors can trade, enabling sandwich attacks, compliance violations, and value extraction from LP positions that the pool admin specifically intended to restrict. The allowlist guard fails completely open for all router-mediated swaps once the router is allowlisted.

## Likelihood Explanation

The pool admin must allowlist the router to let their approved users access periphery swap functions — without this step, allowlisted users are forced to call the pool directly. The moment the admin takes this expected operational step, the bypass is live for all users. No special privilege is required; the exploit is reachable through the standard public router entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`).

## Recommendation

`SwapAllowlistExtension.beforeSwap` should gate on the original end-user, not the immediate pool caller. The simplest safe fix is to align with the deposit extension pattern: identify the actor whose economic position is affected. For swaps, that actor is the address that initiated the router call. Since the pool's `msg.sender` is always the immediate caller, the original caller identity must be threaded through `extensionData` — the router should encode `msg.sender` there, and the extension should decode and check it. Alternatively, document that allowlisting the router opens the gate to all users and provide a separate trusted-forwarder mechanism that forwards original caller identity.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed to swap.
3. Admin calls `setAllowedToSwap(pool, address(router), true)` — to let Alice use the router.
4. Bob (not allowlisted) calls `router.exactInputSingle(ExactInputSingleParams{pool: pool, ...})`.
5. The router calls `pool.swap(recipient, zeroForOne, amount, ...)` with `msg.sender = router`.
6. The pool calls `_beforeSwap(sender=router, ...)`.
7. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully on the curated pool, bypassing the allowlist entirely. [5](#0-4) [6](#0-5)

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
