Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Actual User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the actual user. If the pool admin allowlists the router — a natural step to enable router-based swaps for curated pools — every user, including those not on the allowlist, can bypass the curation gate by routing through the router.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` value and dispatches it verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly — so `msg.sender` to the pool is the router, not the user: [4](#0-3) 

The same pattern holds for `exactInput`, `exactOutputSingle`, and `exactOutput`: [5](#0-4) 

The allowlist lookup therefore resolves to `allowedSwapper[pool][router]` — the router's allowlist status — rather than the individual user's status. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores its `sender` parameter and checks `owner` instead, so the deposit path does not share this flaw: [6](#0-5) 

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router address inadvertently opens the pool to all users. Any address — including those explicitly excluded from the allowlist — can call `MetricOmmSimpleRouter.exactInputSingle` and the extension will pass because it sees the allowlisted router, not the disallowed user. The curation boundary is completely nullified for router-mediated swaps, allowing unauthorized parties to trade on pools intended to be restricted (e.g., KYC-gated, institutional, or compliance-restricted pools). This constitutes broken core pool functionality: the allowlist extension, a primary access-control mechanism, is rendered ineffective for the protocol's primary swap entrypoint.

## Likelihood Explanation

The router is the primary user-facing swap entrypoint deployed by the protocol. A pool admin who wants their allowlisted users to be able to swap via the standard UI/router will naturally add the router to the allowlist. The admin has no on-chain signal that doing so opens the gate to all users; the extension's interface gives no indication that `sender` is the router rather than the end user. The trigger is a routine, well-motivated admin action rather than an exotic configuration.

## Recommendation

The extension must recover the true end-user identity rather than relying on the `sender` argument. The preferred fix is to change `SwapAllowlistExtension.beforeSwap` to check `allowedSwapper[msg.sender][recipient]` (the second parameter, currently unnamed/ignored). This correctly identifies the beneficiary of the swap regardless of routing path, and is consistent with how `DepositAllowlistExtension` already uses `owner` instead of `sender`. Pool documentation should be updated to state that the allowlist gates the swap recipient, not the caller.

## Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension as extension1, beforeSwap order = 1
  admin calls swapExt.setAllowedToSwap(pool, router, true)   // allowlist the router
  admin does NOT allowlist attacker

Attack:
  attacker (not allowlisted) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=attacker, ...)   [msg.sender to pool = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (passes)
        → swap executes, attacker receives output tokens

Result: attacker bypasses the allowlist and trades on a curated pool.
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
