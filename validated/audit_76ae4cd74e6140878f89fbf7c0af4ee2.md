Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the originating user, allowing any caller to bypass per-pool swap allowlists by routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of `MetricOmmPool.swap`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. Any pool admin who allowlists the router address (a necessary step to let their allowlisted users use the router) inadvertently grants every unprivileged user the ability to bypass the allowlist entirely by routing through the public router.

## Finding Description

**Root cause:** `MetricOmmPool.swap` passes `msg.sender` directly as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim as the first argument to the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called the pool — the router, not the originating user: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making `msg.sender` at the pool equal to the router for every user: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit flow:**
```
Setup:
  allowedSwapper[pool][alice]  = true   // alice is KYC'd
  allowedSwapper[pool][router] = true   // admin allowlists router so alice can use it
  allowedSwapper[pool][eve]    = false  // eve is NOT allowlisted

Attack:
  eve → MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
       → pool.swap(msg.sender = router, ...)
       → _beforeSwap(sender = router, ...)
       → SwapAllowlistExtension.beforeSwap(sender = router)
            allowedSwapper[pool][router] == true  ✓  (passes)
       → swap executes for eve
```

No existing guard prevents this. The extension has no mechanism to distinguish the originating user from the router intermediary, and the router passes no originating-user identity in `extensionData`.

## Impact Explanation

This is a direct admin-boundary break. A pool admin who deploys a curated pool (KYC-gated, institutional-only, or regulatory-restricted) and configures `SwapAllowlistExtension` loses the entire access-control guarantee the moment they also allowlist the router. Any unprivileged user can execute swaps on the curated pool by calling the public, permissionless `MetricOmmSimpleRouter`. The pool's curation policy is completely nullified, matching the contest's "Admin-boundary break" and "Allowlist path" allowed-impact categories.

## Likelihood Explanation

The trigger requires the pool admin to allowlist the router address. This is a natural and expected operational step: without it, allowlisted users cannot use the router at all (their swaps revert because `allowedSwapper[pool][router] == false`). Any admin who wants to offer router UX to their allowlisted users will take this step, unknowingly opening the bypass to all users. The router is a public, permissionless contract, so once it is allowlisted the bypass is immediately available to every address on-chain. No special privileges or unusual conditions are required for the attacker.

## Recommendation

The extension must gate on the originating user, not the immediate pool caller. Two complementary approaches:

1. **Pass the original user through the router.** `MetricOmmSimpleRouter` should encode `msg.sender` into `extensionData` before calling `pool.swap`, and `SwapAllowlistExtension.beforeSwap` should decode and check that address when `sender` is a recognized router.

2. **Router registry in the extension.** The extension can maintain a registry of trusted routers and, when `sender` is a known router, require `extensionData` to carry a verified original-user address and check `allowedSwapper[pool][originalUser]` instead of `allowedSwapper[pool][router]`.

The simplest production fix is option 1: have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a recognized router address.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension configured
// 2. Pool admin calls setAllowedToSwap(pool, alice, true)
// 3. Pool admin calls setAllowedToSwap(pool, address(router), true)
//    (necessary so alice can use the router)
// 4. Eve (not allowlisted) calls:

router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: eve,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));

// Result: swap executes for eve
// allowedSwapper[pool][router] == true passes the check
// eve is not individually allowlisted but bypasses the guard
```

Verify with a Foundry integration test: deploy pool + extension, configure allowlist as above, call `exactInputSingle` from an un-allowlisted address, assert the swap succeeds (no `NotAllowedToSwap` revert).

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
