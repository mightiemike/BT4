Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is the pool's own `msg.sender`. When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router contract, so the extension checks whether the router is allowlisted rather than the original user. Any pool admin who allowlists the router to enable standard UX for their curated users inadvertently opens the pool to every user on-chain.

## Finding Description
**Exact call chain:**

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whatever the pool received as its own `msg.sender` — the router when routed through `MetricOmmSimpleRouter`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no mechanism to forward the original user identity — `msg.sender` stored in transient storage is used only for the payment callback, not passed to the pool's `swap()` call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. There is no existing guard that recovers the original initiator's identity before the allowlist check.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is a curated pool — the admin intends to restrict trading to specific counterparties (e.g., KYC'd users, designated market makers). If the admin allowlists the router (the natural step to enable standard UX), any unprivileged user can call `router.exactInputSingle()` and trade on the restricted pool. This allows unauthorized parties to extract value from LP positions that were priced and sized for a controlled set of counterparties, causing direct loss of LP principal and fees. The admin faces an impossible configuration choice: either allowlisted users cannot use the router at all, or the allowlist is completely bypassed for all users.

**Severity: High** — direct loss of LP funds, no external conditions required beyond the pool admin making the natural configuration choice of allowlisting the router.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who deploys a `SwapAllowlistExtension` pool and wants their allowlisted users to access it through the standard router will allowlist the router. The existing test suite allowlists `TestCaller` contracts (which call the pool directly) rather than the router, so the router-mediated bypass scenario is not covered and the misconfiguration is not surfaced: [5](#0-4) 

The misconfiguration is therefore the expected, natural configuration, not an edge case.

## Recommendation
The `SwapAllowlistExtension` must gate the economically relevant actor — the end user — not the intermediary contract. Two sound approaches:

1. **Pass the original initiator through the router.** The router already stores `msg.sender` in transient storage for the callback payer context (`_setNextCallbackContext(..., msg.sender, ...)`). Extend this to pass the original user as an additional field in `extensionData`, and have the extension decode and check it. This requires a protocol-level convention for the first bytes of `extensionData`.

2. **Check `tx.origin` as a fallback.** When `sender` is a known router, fall back to `tx.origin`. This is safe in this context because the router is a trusted periphery contract and the check is purely for allowlisting, not for fund custody.

3. **Document that the router must never be allowlisted** and that allowlisted users must call the pool directly. This is the weakest mitigation and breaks the intended UX.

## Proof of Concept
```solidity
// Pool admin sets up a restricted pool with SwapAllowlistExtension
// Admin allowlists alice and the router (to let alice use the router)
ext.setAllowedToSwap(pool, alice, true);
ext.setAllowedToSwap(pool, address(router), true);  // ← natural step

// Attacker (not allowlisted) bypasses the allowlist via the router
vm.prank(attacker);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1_000e18,
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Swap succeeds — extension saw sender=router (allowlisted), not attacker
```

The pool calls `_beforeSwap(msg.sender=router, ...)`, the extension checks `allowedSwapper[pool][router]` which is `true`, and the swap proceeds despite `attacker` not being allowlisted. [6](#0-5) [1](#0-0)

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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
