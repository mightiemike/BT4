Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Evaluates Router Address as Swapper, Allowing Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates pool swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates the call, `sender` resolves to the router contract address. Any pool that allowlists the router (required for router-based swaps to function) simultaneously grants unrestricted swap access to every user who routes through it, completely defeating per-user allowlist enforcement.

## Finding Description
The call chain is confirmed in production code:

**Step 1** — `MetricOmmPool.swap()` captures `msg.sender` and passes it as `sender` to `_beforeSwap`: [1](#0-0) 

**Step 2** — `ExtensionCalling._beforeSwap()` encodes that `sender` value and dispatches it to every configured extension via `_callExtensionsInOrder`: [2](#0-1) 

**Step 3** — `SwapAllowlistExtension.beforeSwap()` enforces the guard against `sender` (the direct pool caller), not the originating user. `msg.sender` inside the extension is the pool, and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 4** — `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`. If the pool admin has allowlisted the router (which is structurally required for any router-mediated swap to succeed, since the pool calls `metricOmmSwapCallback` on `msg.sender`), the check passes unconditionally for every user who routes through it. The router does not encode any end-user identity into `extensionData`; it passes the caller-supplied `params.extensionData` verbatim, which cannot be trusted for identity without a signature scheme. [5](#0-4) 

## Impact Explanation
Any pool that simultaneously deploys `SwapAllowlistExtension` to restrict swaps to a curated set of addresses and allowlists `MetricOmmSimpleRouter` so that legitimate users can route through it is fully open to any address that calls through the router. The allowlist provides zero per-user protection. This enables unauthorized participants to drain liquidity from a private or permissioned pool, bypass KYC/compliance gates the pool admin believed were enforced, and cause LP principal loss if the pool's pricing or depth was calibrated for a specific trusted counterparty set. This satisfies the **admin-boundary break** and **broken core pool functionality** impact categories.

## Likelihood Explanation
The scenario is not hypothetical. Any production deployment that configures `SwapAllowlistExtension` on a pool and needs the router to work (the standard user-facing entry point) must allowlist the router. The pool admin has no mechanism to simultaneously allow router-based swaps and enforce per-user restrictions with the current extension design. The conflict is structural and will be encountered by any operator who tries to use both features together. No special attacker capability is required beyond calling the public router.

## Recommendation
The extension should check the end user rather than the direct pool caller. Two concrete options:

1. **Check `recipient`** — `recipient` is the address that receives output tokens and is passed as the second argument to `beforeSwap`. For single-hop router-mediated swaps this is the actual user. Change the guard to `allowedSwapper[msg.sender][recipient]`. Note this does not fully cover multi-hop paths where intermediate hops use the router as recipient. [3](#0-2) 

2. **Pass end-user identity via `extensionData`** — The router encodes the originating user address into `extensionData`; the extension decodes and verifies it (with a signature or trusted-forwarder pattern). This is the more robust fix for multi-hop paths.

## Proof of Concept
```
Setup
─────
1. Pool P is deployed with SwapAllowlistExtension E.
2. Admin calls E.setAllowedToSwap(P, router, true)
   // router allowlisted so users can route (required for callback to succeed)
3. Admin does NOT call E.setAllowedToSwap(P, alice, true)
   // alice is NOT allowlisted

Attack
──────
4. Alice calls MetricOmmSimpleRouter.exactInputSingle(..., pool=P, recipient=alice, ...)
5. Router calls P.swap(recipient=alice, ..., extensionData)
   → msg.sender inside P = router
6. P calls _beforeSwap(sender=router, recipient=alice, ...)
7. E.beforeSwap(sender=router, ...) evaluates:
      allowedSwapper[P][router] == true  ✓  → no revert
8. Swap executes. Alice receives tokens from a pool she was explicitly excluded from.
```

The guard never inspects `alice`; it only sees `router`, which is allowlisted. The allowlist is fully bypassed. [6](#0-5) [7](#0-6)

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
