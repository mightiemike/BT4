Audit Report

## Title
`SwapAllowlistExtension` checks the immediate caller (`sender`) rather than the originating user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router becomes `sender`. If the pool admin allowlists the router to support standard UX, every address on-chain can bypass the swap allowlist unconditionally by calling the router. The guard checks the intermediary, not the economically relevant actor.

## Finding Description

`MetricOmmPool.swap()` always passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` at line 72-80, so `sender` = router address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The pool admin faces an inescapable dilemma: if the router is not allowlisted, no user can swap through the router (broken UX); if the router is allowlisted, every address on-chain can bypass the allowlist by routing through the public, permissionless `MetricOmmSimpleRouter`.

This is structurally different from `DepositAllowlistExtension`, which correctly checks `owner` (the position owner, explicitly passed by the caller) rather than `sender` (the immediate caller):

```solidity
// DepositAllowlistExtension.sol L38
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
```

The deposit extension gates the right identity regardless of whether the liquidity adder is used. The swap extension does not — there is no equivalent `recipient` or originating-user parameter checked.

The `_beforeSwap` dispatcher in `ExtensionCalling.sol` does pass `recipient` as the second argument to `beforeSwap`, but `SwapAllowlistExtension.beforeSwap` ignores it (the second parameter is unnamed `address`). The originating user's identity is structurally absent from the check.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` for KYC compliance, private market-making, or institutional access control is fully bypassable by any user routing through the public `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against restricted LP liquidity, draining LP capital at oracle-anchored prices the pool admin intended to reserve for vetted counterparties. This breaks the core pool access-control invariant — the swap allowlist extension is the designated mechanism for restricting swap access, and it fails entirely when the router is allowlisted. This constitutes broken core pool functionality with direct LP fund exposure.

## Likelihood Explanation

The bypass requires only that the pool admin has allowlisted the router — a natural and necessary operational step for any pool that wants to support standard UX through `MetricOmmSimpleRouter`. Any user who calls `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` on a router-allowlisted pool bypasses the guard unconditionally. No special privileges, flash loans, or oracle manipulation are required. The attacker only needs to call a public, permissionless contract.

## Recommendation

Gate on the originating user, not the immediate caller. Two viable approaches:

1. **Check `recipient` instead of `sender`**: Since `recipient` is the address that receives output tokens and is the economically relevant actor for a swap, the extension could check `allowedSwapper[pool][recipient]`. The router always passes the user-supplied `recipient` directly to `pool.swap()`.

2. **Pass user identity through `extensionData`**: Have the router encode the originating user's address in `extensionData` and have the extension decode and verify it. This requires the extension to trust that the pool's `msg.sender` is a known router that correctly encodes the user.

## Proof of Concept

```
1. Pool P is deployed with SwapAllowlistExtension E.
2. Pool admin calls E.setAllowedToSwap(P, alice, true)       // alice is KYC'd
3. Pool admin calls E.setAllowedToSwap(P, router, true)      // enable router UX
4. Bob (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(P, ...)
5. Router calls P.swap(recipient=bob, ...)  [MetricOmmSimpleRouter.sol L72-80]
6. P passes msg.sender=router as `sender` to _beforeSwap     [MetricOmmPool.sol L230-231]
7. E.beforeSwap checks allowedSwapper[P][router] == true → passes  [SwapAllowlistExtension.sol L37]
8. Bob's swap executes against restricted LP liquidity.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, allowlist only `alice` and the router, call `MetricOmmSimpleRouter.exactInputSingle` as `bob`, assert the swap succeeds (no `NotAllowedToSwap` revert) and `bob` receives output tokens. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
