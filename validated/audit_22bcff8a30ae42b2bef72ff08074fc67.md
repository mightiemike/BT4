Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end-user, allowing any non-allowlisted user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` parameter, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is the entity that calls `pool.swap()`, so `sender` becomes the router address rather than the end-user. Any pool admin who allowlists the router — the only way to permit router-mediated swaps on an allowlisted pool — inadvertently grants every on-chain user the ability to bypass the per-user allowlist gate.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

Therefore, when any user calls any router swap function, the allowlist lookup becomes `allowedSwapper[pool][router]`. If the admin has allowlisted the router (required for any router-mediated swap to succeed), this check passes for every user regardless of their individual allowlist status.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on the `owner` parameter — the actual economic beneficiary — rather than the direct caller: [6](#0-5) 

## Impact Explanation
Any non-allowlisted user can execute swaps against a pool the admin intended to restrict. This breaks the core allowlist invariant and allows unauthorized parties to trade against pool liquidity — draining bins, front-running allowlisted LPs, or executing swaps the pool operator explicitly prohibited. The result is unauthorized swap settlement against LP assets, constituting a direct loss-of-control over pool access with fund-impacting consequences.

## Likelihood Explanation
The trigger is a valid, expected admin action: allowlisting the router so that allowlisted users can swap through the standard periphery. Any pool using `SwapAllowlistExtension` that also wants router support is affected. No privileged attacker role is required — any public user can call `MetricOmmSimpleRouter` once the router is allowlisted by the admin.

## Recommendation
The `beforeSwap` hook must gate on the end-user identity, not the direct caller. Three options:

1. **Pass the end-user through `extensionData`**: The router encodes the original `msg.sender` into `extensionData`; the extension decodes and checks it. Requires a trusted-router assumption.
2. **Check `recipient` instead of `sender`**: If the pool's swap design guarantees `recipient` is the economic beneficiary for all router call patterns, gate on that field instead.
3. **Dedicated router allowlist + per-user check inside the router**: The extension allowlists the router as a trusted forwarder; the router enforces its own per-user allowlist before calling the pool.

The cleanest fix matching the `DepositAllowlistExtension` pattern (use one canonical identifier representing the actual user) is option 1 or 2.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {Test} from "forge-std/Test.sol";
import {SwapAllowlistExtension} from
    "metric-periphery/contracts/extensions/SwapAllowlistExtension.sol";
import {AllowlistFactoryStub} from "metric-periphery/test/AllowlistFactoryStub.sol";

contract SwapAllowlistBypassTest is Test {
    AllowlistFactoryStub factoryStub;
    SwapAllowlistExtension extension;
    address pool    = makeAddr("pool");
    address admin   = makeAddr("admin");
    address router  = makeAddr("router");   // MetricOmmSimpleRouter
    address badUser = makeAddr("badUser");  // NOT on the allowlist

    function setUp() public {
        factoryStub = new AllowlistFactoryStub();
        factoryStub.setPoolAdmin(pool, admin);
        extension = new SwapAllowlistExtension(address(factoryStub));

        // Admin allowlists the router so that allowlisted users can swap via router
        vm.prank(admin);
        extension.setAllowedToSwap(pool, router, true);

        // badUser is explicitly NOT allowlisted
        assertFalse(extension.isAllowedToSwap(pool, badUser));
    }

    function test_nonAllowlistedUserBypassesViaRouter() public {
        // Pool calls beforeSwap; sender = router (router called pool.swap())
        // badUser is the actual end-user but is invisible to the extension
        vm.prank(pool);
        // Does NOT revert — badUser bypasses the allowlist via the router
        extension.beforeSwap(
            router,     // sender = router, not badUser
            badUser,    // recipient (ignored by the check)
            false, 0, 0, 0, 0, 0, ""
        );
    }
}
```

The test passes without revert, confirming that `badUser` — not on the allowlist — can execute a swap on a restricted pool by routing through `MetricOmmSimpleRouter`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
