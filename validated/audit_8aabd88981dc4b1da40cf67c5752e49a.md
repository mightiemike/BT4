Audit Report

## Title
`SwapAllowlistExtension` checks `sender` (router address) instead of actual user, enabling per-user allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter against `allowedSwapper[pool][sender]`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router contract address — not the originating user. A pool admin cannot simultaneously allowlist individual users and support router-based swaps: allowlisting the router grants access to every user regardless of their individual allowlist status, while not allowlisting the router blocks all individually-allowlisted users from swapping via the router.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value directly to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` inside the pool: [4](#0-3) 

Therefore `sender` inside `beforeSwap` equals the router address, not the originating user. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` explicitly ignores `sender` (first argument unnamed) and checks `owner` — the beneficial owner of the position: [5](#0-4) 

The asymmetry is the root cause: deposits gate by beneficial owner; swaps gate by the direct caller (the router).

## Impact Explanation

The pool admin faces a forced binary choice with no correct option:

1. **Allowlist the router** (`allowedSwapper[pool][router] = true`): the check passes for every user who calls the router, regardless of individual allowlist status. Any unprivileged user can swap in a pool intended to be private or KYC-gated. The per-user allowlist is completely bypassed.

2. **Do not allowlist the router**: all router-based swaps revert with `NotAllowedToSwap()` even for individually allowlisted users, making the primary user-facing swap entry point unusable for the pool.

This is an admin-boundary break: an unprivileged path (the router) bypasses the access control the pool admin configured. For a private or compliance-gated pool, case (1) allows unauthorized users to interact with a pool that may carry compliance obligations or favorable pricing terms.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any pool that deploys `SwapAllowlistExtension` expecting per-user gating and expects users to interact via the router will encounter this issue. No special permissions are required — any user can call the router. The trigger is the normal, documented swap flow.

## Recommendation

Check `recipient` (the address receiving output tokens, set by the user even when routing through the router) instead of `sender`, mirroring the pattern used by `DepositAllowlistExtension`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router could encode the originating user in `extensionData` and the extension could decode and verify it, though this requires router cooperation and is more complex.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// allowedSwapper[pool][allowedUser] = true
// allowedSwapper[pool][router]      = false

// Case 1: router NOT allowlisted — allowedUser cannot swap via router
vm.prank(allowedUser);
router.exactInputSingle(...); // reverts NotAllowedToSwap — sender=router is not allowlisted

// Case 2: router IS allowlisted — any user bypasses the allowlist
swapAllowlist.setAllowedToSwap(pool, address(router), true);
vm.prank(unauthorizedUser);
router.exactInputSingle(...); // succeeds — sender=router is allowlisted, user check skipped
assertEq(allowedSwapper[pool][unauthorizedUser], false); // user was never allowlisted
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
