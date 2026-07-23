Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the proximate caller rather than the end user, allowing any address to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on `sender`, which is `msg.sender` of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router's address. A pool admin who allowlists the router so that their individually-allowlisted users can use the standard interface inadvertently opens the pool to every user on the network, because `allowedSwapper[pool][router] == true` satisfies the guard for all callers regardless of who initiated the router call.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of the pool: [4](#0-3) 

The extension never sees the end user's address. This creates an inescapable dilemma for the pool admin: not allowlisting the router blocks all router users (including individually-allowlisted ones); allowlisting the router opens the pool to every user on the network.

Note: The claim that `DepositAllowlistExtension` has the same flaw is incorrect. `DepositAllowlistExtension.beforeAddLiquidity` ignores `sender` and checks `owner` (the position owner passed to `addLiquidity`), which correctly gates on the economic actor regardless of who the proximate caller is: [5](#0-4) 

## Impact Explanation
Any unprivileged user can execute swaps in a pool the admin intended to restrict to a specific set of addresses. Pools designed for permissioned trading (institutional-only, KYC-gated, whitelist-only) are rendered fully open to the public once the router is allowlisted. This is a direct admin-boundary break: the access-control gate the pool admin explicitly configured is bypassed by an unprivileged actor using the standard, documented periphery interface.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard, documented entry point for swaps in the periphery layer. Any pool admin who wants their allowlisted users to use the normal interface must allowlist the router. The bypass is therefore triggered by the ordinary, expected configuration of a permissioned pool and requires no special privileges, unusual inputs, or knowledge beyond the public interface.

## Recommendation
The extension must check the identity of the economic actor, not the proximate caller. Two sound approaches:

1. **Pass the original user through the router**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value instead of (or in addition to) `sender`.
2. **Check `recipient`**: If the pool's design equates the recipient with the authorized trader, check `recipient` rather than `sender`. This must be validated against the pool's economic model.

## Proof of Concept
```
Setup:
  1. Deploy a pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, alice, true)  — only alice is allowlisted.
  3. Pool admin calls setAllowedToSwap(pool, router, true) — router allowlisted so alice can use it.

Attack:
  4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(pool, ...).
  5. Router calls pool.swap(recipient=bob, ...) → msg.sender = router.
  6. Pool calls _beforeSwap(sender=router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Bob's swap executes successfully despite not being individually allowlisted.

Result:
  - Bob bypasses the per-user allowlist gate.
  - Any user can repeat this, rendering the allowlist ineffective.
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
