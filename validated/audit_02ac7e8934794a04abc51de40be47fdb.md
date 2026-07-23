Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to `SwapAllowlistExtension.beforeSwap`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants unrestricted swap access to every caller of the router, regardless of individual allowlist status.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim as the first argument of `IMetricOmmExtensions.beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)`, the pool's `msg.sender` is the router contract address — not the originating user: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. A pool admin who wants allowlisted users to use the router must allowlist the router address. Once the router is allowlisted, every caller of the router passes the check — the extension decision is wrong for every non-allowlisted user who routes through it.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates by the explicitly supplied `owner` argument, which is actor-stable across periphery paths: [5](#0-4) 

The asymmetry is the root cause: the `owner` field in `addLiquidity` is an explicit parameter that cannot be spoofed by routing; the `sender` field in `swap` is derived from `msg.sender` and collapses to the router address for all router-mediated swaps.

## Impact Explanation

Any pool configured with `SwapAllowlistExtension` for per-user curation (KYC-only trading, institutional-only pools, whitelist-gated programs) is fully bypassed by any user who routes through `MetricOmmSimpleRouter`. The pool admin has no on-chain mechanism to simultaneously allow legitimate allowlisted users to use the router and block non-allowlisted users from using the same router. The core curation invariant of the extension — that only explicitly approved addresses may swap — is broken. This constitutes broken core pool functionality causing unauthorized access to restricted pools, matching the allowed impact gate for broken core pool functionality.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point for the protocol. Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting and expects users to interact via the router is affected. The bypass requires only a standard public router call — no flash loans, no reentrancy, no privileged access. The attacker needs only to call `exactInputSingle` (or any other router entry point) targeting the restricted pool.

## Recommendation

Gate the swap allowlist on the end user rather than the immediate pool caller. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The extension must verify `msg.sender` is a known trusted router before trusting the payload to prevent spoofing.

2. **Mirror the deposit extension pattern**: Add an explicit `swapper` parameter to the pool's swap interface (analogous to `owner` in `addLiquidity`) that the caller must supply and that the pool enforces matches `msg.sender` or a delegated address. The extension then checks that field instead of `sender`.

Until fixed, pool admins must be warned that allowlisting the router address grants unrestricted swap access to all router users, and that per-user swap gating only works when users call the pool directly.

## Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E configured on beforeSwap.
2. Admin allowlists only Alice: setAllowedToSwap(P, Alice, true).
3. Admin allowlists the router to enable router-based swaps:
       setAllowedToSwap(P, router, true).
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: P, ...})
5. Router calls pool.swap(...) — pool's msg.sender = router.
6. Pool calls E.beforeSwap(sender=router, ...).
7. Extension checks allowedSwapper[P][router] == true → passes.
8. Bob's swap executes in the curated pool despite never being allowlisted.

Alternatively, if the admin does NOT allowlist the router:
1. Admin allowlists Alice: setAllowedToSwap(P, Alice, true).
2. Alice calls router.exactInputSingle({pool: P, ...}).
3. Router calls pool.swap() — pool's msg.sender = router.
4. Extension checks allowedSwapper[P][router] == false → reverts NotAllowedToSwap.
5. Alice cannot use the router at all, even though she is individually allowlisted.
```

A Foundry integration test can confirm both scenarios by deploying a pool with `SwapAllowlistExtension`, allowlisting only Alice and the router, then asserting that an unapproved Bob address successfully executes a swap via `exactInputSingle`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
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
