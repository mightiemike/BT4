Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of End-User, Enabling Complete Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` at the time `pool.swap` is called. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][end_user]`. If the router is allowlisted — the only production configuration that permits legitimate users to swap through the standard periphery — every unprivileged user on the network can bypass the curated-pool restriction with a single router call.

## Finding Description

**Root cause — pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

**Extension dispatch — `sender` forwarded unchanged:**

`ExtensionCalling._beforeSwap` encodes and forwards `sender` verbatim to every configured extension: [2](#0-1) 

**Guard — checks the wrong address:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap` — the router, not the end user: [3](#0-2) 

**Router entry-points — all call `pool.swap` directly:**

`exactInputSingle` calls `pool.swap` with no originating-user context in `extensionData`: [4](#0-3) 

The same pattern applies to `exactInput` (line 104), `exactOutputSingle` (line 136), and `exactOutput` (line 165). In every case the pool sees `msg.sender = router`, so the extension evaluates `allowedSwapper[pool][router]`, never `allowedSwapper[pool][end_user]`. [5](#0-4) 

**Why existing guards are insufficient:**

There is no secondary check on `recipient`, no decoding of `extensionData` to recover the originating user, and no mechanism in the pool interface to distinguish "router acting on behalf of an allowlisted user" from "router acting on behalf of an arbitrary user." The extension API exposes only `sender`, which is structurally the router address for all router-mediated swaps.

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses (e.g., KYC-verified counterparties). The admin must allowlist the `MetricOmmSimpleRouter` for legitimate users to access the pool through the standard periphery. Once the router is allowlisted, the allowlist provides zero protection: any unprivileged address can call any router entry-point and the extension passes the check unconditionally. This is a complete failure of the admin-configured access-control boundary — an admin-boundary break reachable by an unprivileged caller with no special setup.

## Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol.
- The bypass requires no privileges, no flash loans, and no multi-transaction setup: a single `exactInputSingle` call suffices.
- Pool admins have no in-protocol mechanism to avoid the dilemma: allowlisting the router opens the pool to everyone; not allowlisting it breaks the router for legitimate users.
- The condition (router allowlisted) is the natural and expected production state.

## Recommendation

The `sender` forwarded to `beforeSwap` must represent the economic actor, not the intermediary. Two viable fixes:

1. **Extension-data forwarding (preferred):** Require the router to ABI-encode the originating `msg.sender` as the first word of `extensionData`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `extensionData` is non-empty, falling back to `sender` for direct pool calls. This preserves the pool interface and requires only router and extension coordination.

2. **Recipient-based gating:** Gate on `recipient` instead of `sender`. This is semantically correct for curated pools where the economic beneficiary is the recipient, and requires no router changes.

## Proof of Concept

```
Setup:
  - Deploy pool P with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(P, alice, true)
  - Pool admin calls setAllowedToSwap(P, router, true)  ← required for alice to use the router

Attack (bob, not allowlisted):
  1. bob calls router.exactInputSingle({pool: P, recipient: bob, ...})
  2. Router calls P.swap(bob, ...) — pool sees msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap:
       allowedSwapper[P][router] == true  →  check passes
  5. Swap executes; bob receives output tokens

Result:
  bob, who is not on the allowlist, successfully swaps on a curated pool.
  The SwapAllowlistExtension guard is completely bypassed.

Foundry test sketch:
  - deployPool(extensions=[swapAllowlist])
  - swapAllowlist.setAllowedToSwap(pool, alice, true)
  - swapAllowlist.setAllowedToSwap(pool, router, true)
  - vm.prank(bob); router.exactInputSingle(...)
  - assertEq(token1.balanceOf(bob), expectedOut)  // passes — bypass confirmed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
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
```
