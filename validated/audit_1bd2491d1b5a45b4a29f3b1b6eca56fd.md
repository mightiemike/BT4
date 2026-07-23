Audit Report

## Title
`SwapAllowlistExtension.beforeSwap()` evaluates router address as swapper identity, enabling complete per-user allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap()` always populates with its own `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's allowlist status rather than the real user's. Any user can bypass a per-user allowlist by calling the public router instead of the pool directly.

## Finding Description

**Root cause — `MetricOmmPool.swap()` passes `msg.sender` as `sender`:**

`MetricOmmPool.swap()` unconditionally passes `msg.sender` to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` in `ExtensionCalling.sol` encodes that value and dispatches it to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called the pool — the router, not the end user: [3](#0-2) 

**Router path — real user identity is never forwarded to the pool:**

`MetricOmmSimpleRouter.exactInputSingle()` stores the real user (`msg.sender`) only in transient storage for the payment callback via `_setNextCallbackContext`, then calls `pool.swap()` without any user-identity argument: [4](#0-3) 

The pool's `swap()` interface has no `swapper` parameter — it only accepts `recipient`, so there is no channel through which the router can communicate the real user's identity to the extension hook: [5](#0-4) 

**Exploit flow:**

```
forbiddenUser → MetricOmmSimpleRouter.exactInputSingle()
              → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
              → SwapAllowlistExtension.beforeSwap(sender=router, ...)
              → allowedSwapper[pool][router] == true  ✓ (bypass)
```

A direct call by `forbiddenUser` correctly hits `allowedSwapper[pool][forbiddenUser] == false` and reverts. The router intermediary changes the identity seen by the guard.

## Impact Explanation

**Allowlist bypass (fund-impacting):** A pool admin deploying `SwapAllowlistExtension` to restrict swaps to KYC'd counterparties, institutional traders, or whitelisted market makers must allowlist the router for any router-mediated swap to succeed. Once the router is allowlisted, every user — including those explicitly excluded — can bypass the per-user gate by calling `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`. The allowlist is rendered completely ineffective for the router path, allowing unauthorized or toxic flow into pools explicitly designed to exclude it, directly harming LP value.

**DoS (broken core swap flow):** If the admin does not allowlist the router, every allowlisted user who attempts to swap through the router is denied with `NotAllowedToSwap`, making the router path permanently broken for all allowlisted pools.

Both outcomes break the core security guarantee of `SwapAllowlistExtension`.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool deploying `SwapAllowlistExtension` and expecting users to interact through the router will encounter this mismatch on every router-mediated swap. The behavior is deterministic, requires no special conditions, and is reachable by any unprivileged user with access to the public router.

## Recommendation

The `sender` identity passed through the hook chain must reflect the actual end user, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Extend the swap interface**: Add an explicit `swapper` parameter to `pool.swap()` that the router populates with `msg.sender` before calling the pool. The pool passes this value (rather than its own `msg.sender`) as `sender` to extension hooks.

2. **Extension-side resolution via `extensionData`**: Have `SwapAllowlistExtension.beforeSwap()` accept an `extensionData` payload that the router encodes with the real user address, verified against a trusted forwarder registry or router-signed attestation.

Option 1 is simpler and keeps the trust model intact because the pool still controls what `sender` value reaches the extension.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// metric-periphery/test/extensions/SwapAllowlistBypassViaRouter.t.sol

import {MetricOmmPoolBaseTest, MockPriceProvider} from "@metric-core-test/MetricOmmPool.base.t.sol";
import {SwapAllowlistExtension} from "../../contracts/extensions/SwapAllowlistExtension.sol";
import {MetricOmmSimpleRouter} from "../../contracts/MetricOmmSimpleRouter.sol";
import {IMetricOmmSimpleRouter} from "../../contracts/interfaces/IMetricOmmSimpleRouter.sol";
import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";

contract SwapAllowlistBypassViaRouterTest is MetricOmmPoolBaseTest {
    SwapAllowlistExtension swapExtension;
    MetricOmmSimpleRouter router;
    address forbiddenUser = makeAddr("forbiddenUser");

    function setUp() public override {
        super.setUp();
        swapExtension = new SwapAllowlistExtension(factory);
        router = new MetricOmmSimpleRouter(address(0), factory);
        // deploy pool with SwapAllowlistExtension on beforeSwap, seed liquidity
        // allowlist router (required for router path to work), NOT forbiddenUser
        swapExtension.setAllowedToSwap(address(pool), address(router), true);
        deal(address(token0), forbiddenUser, 1e18);
        vm.prank(forbiddenUser);
        token0.approve(address(router), type(uint256).max);
    }

    // Direct swap by forbiddenUser correctly reverts
    function test_directSwap_forbiddenUser_reverts() public {
        vm.prank(forbiddenUser);
        vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
        pool.swap(forbiddenUser, false, int128(1000), type(uint128).max, "", "");
    }

    // Router-mediated swap by forbiddenUser bypasses the allowlist
    function test_routerSwap_forbiddenUser_bypasses_allowlist() public {
        // Should revert but SUCCEEDS — extension sees sender=router (allowlisted)
        vm.prank(forbiddenUser);
        router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool), recipient: forbiddenUser, zeroForOne: false,
            amountIn: 1000, amountOutMinimum: 0, priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1, tokenIn: address(token0), extensionData: ""
        }));
        // forbiddenUser received output tokens — allowlist bypassed
    }
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L188-195)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external returns (int128 amount0Delta, int128 amount1Delta);
```
