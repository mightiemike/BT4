Audit Report

## Title
`SwapAllowlistExtension::beforeSwap` checks the router's address instead of the end user's address, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router contract address, not the end user. If the pool admin allowlists the router (the natural step to enable router-based swaps for allowlisted users), every user — including those not on the allowlist — can bypass the swap guard by routing through the router.

## Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the pool calls the extension). `sender` is the first argument forwarded from `ExtensionCalling._beforeSwap`:

In `MetricOmmPool.swap` (line 230–240), `_beforeSwap` is called with `msg.sender` as the first argument:

```solidity
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,   // recipient = end user
    ...
);
```

The pool's `msg.sender` is the router, so `sender` = router address. The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The `beforeSwap` interface receives `recipient` (the actual end user) as the second argument, but `SwapAllowlistExtension` silently discards it — the second parameter is unnamed:

```solidity
// SwapAllowlistExtension.sol line 31
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
```

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the economically relevant actor), not `sender`, demonstrating the intended pattern was understood for deposits but not applied to swaps.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific users (KYC-gated users, institutional counterparties, whitelisted protocols) can be fully bypassed by any user routing through the public, permissionless `MetricOmmSimpleRouter`. Unauthorized users can execute swaps against the pool, violating the pool's intended access-control policy and potentially draining LP funds through unfavorable trades. This constitutes broken core pool functionality causing loss of funds and a broken access-control invariant — a direct match to the allowed impact gate.

## Likelihood Explanation

High. `MetricOmmSimpleRouter` is a public, permissionless periphery contract. Any user can call it. The pool admin is likely to allowlist the router to enable normal user flows (otherwise allowlisted users cannot use the router at all), inadvertently enabling the bypass for every non-allowlisted user. No special privileges or malicious setup are required — only a standard router call.

## Recommendation

The extension must check the economically relevant actor — the end user — rather than the immediate caller. The cleanest fix is to check `recipient` (the second, currently discarded argument) instead of `sender` when the swap is router-mediated, or require the router to encode the original caller in `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and gate on that address. Alternatively, the pool's `swap` function could accept an explicit `originator` parameter that the router populates with the end user's address and forwards to extension hooks as a distinct argument.

## Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists the router so that allowlisted users can swap via the router: `swapExtension.setAllowedToSwap(pool, address(router), true)`.
3. Non-allowlisted user `attacker` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ..., recipient=attacker, ...)`.
4. Router calls `pool.swap(recipient=attacker, ...)` — pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, recipient=attacker, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` (router is allowlisted).
7. Swap executes successfully. `attacker` was never on the allowlist, yet the guard passed.

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
