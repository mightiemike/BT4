Audit Report

## Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Router Address Checked Instead of Actual Swapper - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router becomes that direct caller, so the allowlist check resolves to `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][actualUser]`. Any pool admin who allowlists the router to support router-mediated swaps simultaneously grants unrestricted swap access to every unprivileged user who calls the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` verbatim as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router directly calls `pool.swap(...)` with no mechanism to forward the original `msg.sender`: [3](#0-2) 

The router stores the original caller only in transient storage as the payer for the payment callback, but never exposes it to the pool or extensions. The allowlist lookup therefore becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Once the router is allowlisted, the check passes for every caller of the public router regardless of whether that caller is individually allowlisted.

The same pattern applies to `exactInput` (line 103–112) and `exactOutputSingle`/`exactOutput` (lines 135–137, 165–181). [4](#0-3) 

## Impact Explanation
`SwapAllowlistExtension` is the protocol's primary per-pool access-control mechanism (KYC-verified counterparties, institutional LPs, whitelisted market makers). A complete bypass means unauthorized users can execute swaps on pools intended to be private, exposing LP funds to the full public and defeating the pool admin's access-control intent. This constitutes broken core pool functionality with direct fund-exposure consequences for LP assets in restricted pools, matching the "Broken core pool functionality causing loss of funds or unusable swap flows" allowed impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract — any user can call it. A pool admin who wants to support router-mediated swaps for allowlisted users is forced to allowlist the router address, which immediately opens the gate to all users. There is no in-protocol mechanism to forward the original `msg.sender` through the router hop. The existing unit tests for `SwapAllowlistExtension` only test direct pool calls (`vm.prank(address(pool))`), not router-mediated calls, so this path is untested and the bypass is undetected. [5](#0-4) 

## Recommendation
The extension must gate on end-user identity, not the direct caller of `pool.swap`. Two complementary approaches:

1. **Pass the original user through the router.** Encode the original `msg.sender` in `extensionData` (e.g., `abi.encode(msg.sender)`) before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` can then decode and verify the true initiator when `sender` is a known router.

2. **Trusted-router registry in the extension.** Add a `trustedRouter` mapping to `SwapAllowlistExtension`. When `sender` is a trusted router, extract the real user from `extensionData` and check `allowedSwapper[pool][realUser]` instead.

Until fixed, pools relying on `SwapAllowlistExtension` for access control must not allowlist the router address, accepting that router-mediated swaps are unavailable for those pools.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool, ...})
  2. Router calls pool.swap(recipient, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes
  5. Swap executes successfully for the non-allowlisted attacker.

Expected: revert NotAllowedToSwap
Actual:   swap succeeds
```

Foundry test: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)`, then `vm.prank(attacker); router.exactInputSingle(...)` and assert the swap succeeds despite `allowedSwapper[pool][attacker] == false`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

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

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
