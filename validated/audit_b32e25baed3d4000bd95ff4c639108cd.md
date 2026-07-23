Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension checks whether the **router** is allowlisted rather than the actual user. Any pool admin who allowlists the router to enable standard periphery usage inadvertently grants every unprivileged user the ability to bypass the allowlist entirely.

## Finding Description
The call chain is as follows:

1. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` where `msg.sender` is the router. [1](#0-0) 

2. `ExtensionCalling._beforeSwap` encodes `sender = router` and dispatches to the extension via `_callExtensionsInOrder`. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`. The `recipient` parameter (second argument, the actual beneficiary) is explicitly ignored with an unnamed `address` parameter. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle` passes `params.recipient` as the swap recipient but passes no user-identity information as `sender` — the pool sees the router as `msg.sender`. [4](#0-3) 

The dilemma for pool admins is binary and inescapable:
- Router **not** allowlisted → no user (including allowlisted ones) can swap through the standard periphery path.
- Router **is** allowlisted → every user, including non-allowlisted ones, bypasses the guard by routing through `MetricOmmSimpleRouter`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (the router/payer) and checks `owner` (the position beneficiary), which is the right pattern. [5](#0-4) 

## Impact Explanation
**High.** `SwapAllowlistExtension` is a production access-control mechanism designed to restrict trading on curated pools (e.g., institutional, KYC-gated, or counterparty-specific pools) to specific addresses. A non-allowlisted user can trade on any such pool simply by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This completely defeats the purpose of the allowlist, allowing unauthorized parties to extract value from pools intended to be restricted. The bypass is direct, requires no special setup, and is reachable by any public user.

## Likelihood Explanation
**Medium.** The bypass is only exploitable when the pool admin has allowlisted the router. However, allowlisting the router is the natural and expected action for any pool that wants to support the standard user-facing periphery path. Any production deployment using `SwapAllowlistExtension` alongside `MetricOmmSimpleRouter` is likely vulnerable. The audit targets explicitly flag this exact vector. [3](#0-2) 

## Recommendation
`SwapAllowlistExtension` must gate the **original user**, not the intermediary. The preferred fix is to check `recipient` (the second parameter, currently ignored) instead of or in addition to `sender`, following the pattern of `DepositAllowlistExtension` which checks `owner` rather than `sender`. Alternatively, the router can encode `msg.sender` into `extensionData`, and the extension can decode and verify it — but this requires the extension to also verify the `sender` is a trusted router. The simplest correct fix is to check `recipient` as the economically relevant actor receiving output tokens. [5](#0-4) 

## Proof of Concept
```
Setup:
  - Pool deployed with SwapAllowlistExtension active on beforeSwap
  - Pool admin calls: extension.setAllowedToSwap(pool, alice, true)
  - Pool admin calls: extension.setAllowedToSwap(pool, router, true)
    (required so alice can use the router)

Attack:
  - charlie (not allowlisted) calls:
      router.exactInputSingle({
        pool: pool,
        tokenIn: token1,
        tokenOut: token0,
        zeroForOne: false,
        amountIn: 1000,
        recipient: charlie,
        ...
      })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=charlie, msg.sender=router, ...)
      → _beforeSwap(sender=router, recipient=charlie, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, recipient=charlie[ignored], ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes for charlie

Result:
  charlie successfully swaps on a pool he is not allowlisted for.
  The allowlist guard is bypassed.
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
