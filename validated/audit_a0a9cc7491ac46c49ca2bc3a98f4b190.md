Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router becomes `msg.sender` to the pool, so the extension checks whether the **router** is allowlisted rather than the **original user**. If the pool admin allowlists the router (a natural step to enable router-based trading for permitted users), every unprivileged user can bypass the allowlist by calling any of the router's `exact*` entry points.

## Finding Description
**Root cause — wrong actor identity in the extension check:**

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol line 160-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
// msg.sender = pool, sender = router → checks allowedSwapper[pool][router]
```

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly with no mechanism to forward the original caller's identity:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol line 71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Exploit flow:**
1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict swaps to KYC'd or otherwise permitted addresses.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based trading for permitted users (or sets `allowAllSwappers[pool] = true`).
3. An unprivileged, non-allowlisted user calls `router.exactInputSingle(ExactInputSingleParams{pool: curatedPool, ...})`.
4. The router calls `curatedPool.swap(recipient, ...)` with `msg.sender = router`.
5. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
6. The non-allowlisted user's swap executes successfully, bypassing the intended access control.

**Existing guards are insufficient:** The `onlyPool` modifier on `beforeSwap` only verifies the caller is a registered pool; it does not verify the identity of the original swap initiator. The router's `_requireFactoryPool` check only validates that the target pool is registered; it does not inject the original user's address into the pool call.

## Impact Explanation
The swap allowlist is the sole on-chain mechanism for curated pools to restrict trading to permitted participants. A complete bypass allows any unprivileged address to trade on pools intended to be access-controlled, directly violating the pool's curation policy. Depending on the pool's purpose (regulatory compliance, exclusive market-making, whitelist-only liquidity), this constitutes broken core pool functionality and unauthorized access to pool assets. This meets the "admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "broken core pool functionality causing loss of funds or unusable swap flows" impact categories.

## Likelihood Explanation
The bypass requires only that the router be allowlisted on the curated pool, which is the natural and expected configuration for any pool that wants to support router-based trading for its permitted users. The attacker needs no special privileges, no tokens beyond what is needed for the swap, and no coordination — a single public transaction through the router suffices. The attack is repeatable on every block.

## Recommendation
The `SwapAllowlistExtension` must gate on the **original user**, not the immediate pool caller. Two viable approaches:

1. **Pass original caller through `extensionData`:** Have the router encode `msg.sender` into `extensionData` and have the extension decode and check it. This requires a convention between router and extension.
2. **Check `tx.origin` as a fallback for router calls:** Not recommended due to `tx.origin` risks.
3. **Preferred — router-aware allowlist:** Add a separate allowlist entry for "router + original user" pairs, or require the router to be a trusted forwarder that passes the original caller in a standardized field the extension can verify.

The cleanest fix is to have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when the immediate `sender` is a known router.

## Proof of Concept
```solidity
// Foundry test sketch
function test_swapAllowlistBypassViaRouter() public {
    // Setup: curated pool with SwapAllowlistExtension
    // Pool admin allowlists the router (to enable router trading for permitted users)
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    // Do NOT allowlist the attacker
    // assertFalse(swapExtension.isAllowedToSwap(address(pool), attacker));

    // Attacker (not allowlisted) calls router
    vm.prank(attacker);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: type(uint256).max,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Swap succeeds — allowlist bypassed
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
