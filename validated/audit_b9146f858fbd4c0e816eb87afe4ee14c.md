Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End-User, Allowing Allowlist Bypass via Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is the pool's `msg.sender` — the direct caller of `pool.swap(...)`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the end-user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user access, defeating the per-user curation the extension is designed to enforce.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap(), i.e. the router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this `sender` verbatim to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — making the router the pool's `msg.sender`. So `sender` in the extension is the router address, not the end-user (`params.recipient` or the original `msg.sender` of the router call).

The asymmetry with `DepositAllowlistExtension.beforeAddLiquidity` confirms the bug: that extension correctly ignores `sender` (first param) and checks `owner` (second param), which is the actual LP position owner regardless of who calls the pool:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, ...) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The test suite confirms the current behavior: the direct pool caller (`callers[0]`) must be allowlisted, not the end-user (`users[0]`):

```solidity
// metric-periphery/test/extensions/FullMetricExtension.t.sol L70
swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);
_swap(0, users[0], false, int128(1000), type(uint128).max);
```

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict swaps to a known set of users (e.g., KYC-verified addresses, institutional counterparties) is fully bypassed the moment the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter` and swap against the pool as if they were allowlisted. This constitutes broken core pool functionality / curation failure: unauthorized users can interact with a pool configured to serve only specific counterparties, draining liquidity from restricted pools.

## Likelihood Explanation

The router is the standard, documented entry point for swaps. Any pool that (a) deploys `SwapAllowlistExtension` and (b) needs to support router-based swaps must allowlist the router, triggering the bypass automatically. No special attacker capability is required — a standard `MetricOmmSimpleRouter` call suffices. The pool admin faces an impossible choice: allowlist the router (bypass all per-user curation) or don't (router is broken for this pool).

## Recommendation

Gate on the end-user rather than the intermediary. The minimal fix consistent with the existing interface is to check `recipient` instead of `sender` in `beforeSwap`, since `recipient` is the address receiving output tokens and is passed through correctly regardless of routing. Alternatively, mirror the `DepositAllowlistExtension` pattern by having the pool expose an explicit originator field that the router populates with its `msg.sender`.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `beforeSwap`.
2. Pool admin allowlists only `alice` via `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router via `setAllowedToSwap(pool, router, true)` (required for router-based swaps).
4. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. The router calls `pool.swap(recipient=bob, ...)`. The pool passes `msg.sender = router` as `sender` to the extension.
6. The extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `bob` successfully swaps in a pool he was explicitly excluded from.
8. `isAllowedToSwap(pool, bob)` returns `false`, confirming the bypass is invisible to the allowlist read API. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
