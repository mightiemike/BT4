Audit Report

## Title
`SwapAllowlistExtension` checks immediate pool caller (`sender`) instead of economic actor, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against a per-pool allowlist. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so `sender` is the router address — not the actual user. Any pool admin who allowlists the router to permit allowlisted users to trade through it simultaneously opens the gate to every non-allowlisted user, completely defeating the curation guarantee.

## Finding Description
`MetricOmmPool.swap` captures `msg.sender` and forwards it as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` passes that value verbatim as the first argument to `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever address called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly — making the router the `msg.sender` of the pool call — without encoding the original caller into the `sender` position: [4](#0-3) 

The same pattern applies to `exactInput` (intermediate hops use `address(this)` as payer): [5](#0-4) 

And to `exactOutputSingle` and `exactOutput`, which also call `pool.swap()` directly from the router context. [6](#0-5) 

The pool admin faces an impossible choice: if the router is not allowlisted, allowlisted users cannot use it; if the router is allowlisted, every non-allowlisted user can bypass the guard via the router. There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users.

`DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner explicitly supplied by the caller), not `sender` (the immediate pool caller): [7](#0-6) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd counterparties, institutional participants, or specific protocol addresses is completely defeatable by any user routing through `MetricOmmSimpleRouter`. The non-allowlisted user receives the same swap execution as an allowlisted user, including full access to pool liquidity and oracle-priced fills. This is a direct, complete bypass of an access-control invariant that pool admins and LPs rely on to gate counterparty risk — constituting broken core pool functionality and a loss of the curation guarantee the pool admin enforced. This meets the "broken core pool functionality causing loss of funds or unusable swap flows" and "admin-boundary break bypassed by an unprivileged path" impact criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard supported periphery path, deployed and callable permissionlessly by any user. No special role, token balance, or prior state is required. The bypass is a single `exactInputSingle` call targeting the pool address. Any non-allowlisted user can execute this trivially and repeatedly.

## Recommendation
The extension must identify the economic actor — the address whose funds are at risk and whose identity the allowlist is meant to gate — not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated convention between router and extension.
2. **Make the extension router-aware**: The extension checks `sender` when `sender` is not a known router; when `sender` is a known router, it requires the router to attest the real user via an authenticated claim in `extensionData`.

The `DepositAllowlistExtension` pattern (checking an explicitly supplied `owner` field rather than the immediate caller) is the correct model to follow.

## Proof of Concept
1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Admin calls `setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Admin calls `setAllowedToSwap(pool, router, true)` — necessary so Alice can use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `beforeSwap` receives `sender = router`; checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap executes successfully despite not being on the allowlist.

If the admin does **not** add the router in step 3, Alice also cannot use the router — confirming the impossible dilemma. A Foundry integration test can reproduce this by deploying the extension, configuring the allowlist, and asserting that a non-allowlisted caller succeeds via the router when the router is allowlisted.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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
