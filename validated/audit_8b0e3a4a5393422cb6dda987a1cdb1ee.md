Audit Report

## Title
`SwapAllowlistExtension` Allowlist Bypassed via Router: Any User Can Swap Against a Curated Pool - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, the pool sees `msg.sender = router`, so the extension checks `allowedSwapper[pool][router]` rather than the actual end-user's address. A pool admin who allowlists the router to enable router-mediated swaps for their curated users inadvertently grants every public caller of the router unrestricted access to the curated pool.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the immediate caller of `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly with no originator forwarding — the pool's `msg.sender` is the router, not the end-user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The router stores `msg.sender` only in transient callback context for payment purposes, never in `extensionData` or any field visible to the extension: [5](#0-4) 

When the pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps, `allowedSwapper[pool][router] = true` is set. Any unprivileged user calling `router.exactInputSingle` causes the pool to call `extension.beforeSwap(router, ...)`, which evaluates `allowedSwapper[pool][router] == true` and passes — the actual end-user's address is never checked. [6](#0-5) 

## Impact Explanation
A curated pool (KYC-only, institutional-only, or protocol-internal) relying on `SwapAllowlistExtension` to restrict who may trade is fully open to any public user the moment the router is allowlisted. The attacker receives oracle-priced output tokens identical to what any allowlisted trader would receive, constituting unauthorized extraction of LP value and complete nullification of the pool's curation policy. This is a direct loss of LP principal and broken core pool functionality (the allowlist guard), meeting the contest's High/Critical impact threshold.

## Likelihood Explanation
The scenario requires no special privilege. A pool admin who deploys a curated pool and wants their allowlisted users to use the standard router **must** allowlist the router — there is no other supported periphery swap entrypoint and no mechanism in the extension to distinguish the router from a direct caller. The misconfiguration is the expected, natural configuration for any curated pool that also supports router access. The attack is repeatable with zero cost beyond normal swap fees.

## Recommendation
The extension must gate the economically responsible actor, not the immediate pool caller. Viable approaches:

1. **Originator forwarding**: Have the router encode `msg.sender` into `extensionData` and have the extension decode and check that address when `sender` is a known/trusted router address.
2. **Separate intermediary allowlist**: Distinguish "direct swapper allowlist" from "trusted intermediary allowlist"; only trusted intermediaries may forward swaps, and the extension must verify the forwarded identity from `extensionData`.
3. **Block router on curated pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and provide a separate router variant that forwards the originating user address in `extensionData`.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls router.exactInputSingle({pool: pool, ...})
  - router calls pool.swap(recipient, ...) with msg.sender = router
  - pool calls extension.beforeSwap(router, ...) with msg.sender = pool
  - extension checks allowedSwapper[pool][router] == true  → passes
  - swap executes; attacker receives output tokens

Result:
  - attacker, who is NOT on the allowlist, successfully swaps against the curated pool.
  - The SwapAllowlistExtension guard is silently bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
