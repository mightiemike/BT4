Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable standard router-based swaps inadvertently grants every user on the internet the ability to bypass the allowlist, completely defeating the curation invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- always the immediate caller, i.e. the router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value verbatim to every configured extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whatever the pool received — the router address when called via `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  ...
);
```

The pool's `msg.sender` inside this call is the router. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. Since the pool admin must allowlist the router to support any router-based swaps on a curated pool, that single allowlist entry grants every user who calls through the router a passing check.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the `owner` parameter (the economic beneficiary), which the pool passes separately from `msg.sender`, so it is not affected by this flaw.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection entirely for the router path. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle()` and trade against the pool as if they were allowlisted. This is a direct, complete bypass of the curation invariant — the exact wrong value is the extension's `allowedSwapper[pool][sender]` decision, which resolves to `true` for the router instead of the individual user. Depending on pool design, this exposes LP funds to adversarial trading the allowlist was meant to prevent (e.g., regulatory-compliance violations, front-running by non-partners, or arbitrage by excluded addresses).

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary production entry point for swaps. Any pool admin who configures a swap allowlist and also wants to support the standard router must allowlist the router — there is no other supported path. The bypass is reachable by any user on any curated pool that uses the router, with no special privileges required, and is repeatable indefinitely.

## Recommendation
Pass the original user through the swap path so the extension can gate the economically relevant actor. Two concrete options:

1. **Add an `originator` field to the swap call.** The router populates it with `msg.sender` before calling the pool. The pool forwards it to extensions alongside `sender`. `SwapAllowlistExtension` checks `originator` instead of `sender`.
2. **Decode the real user from `extensionData`.** The router injects `abi.encode(msg.sender)` into `extensionData`; the extension decodes and checks it. Less clean but avoids a core interface change.

Either way, the extension must gate the address the pool admin intended to restrict, not the intermediate contract that happens to call the pool.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-based swaps (required for normal operation).
3. `alice` (not individually allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(alice, ...)`. Inside the pool, `msg.sender == router`.
5. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true`. The check passes.
6. Alice's swap executes despite never being added to the allowlist.

Contrast: a direct call `pool.swap(alice, ...)` from Alice's EOA evaluates `allowedSwapper[pool][alice]` → `false` and reverts with `NotAllowedToSwap`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
