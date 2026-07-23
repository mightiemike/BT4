Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Allowing Any Caller to Bypass Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`, and `SwapAllowlistExtension.beforeSwap` checks that router address against the allowlist rather than the actual end user. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged address can bypass the allowlist by calling through `MetricOmmSimpleRouter`. Conversely, if the admin allowlists individual user addresses, those users cannot swap through the router at all.

## Finding Description

**Root cause — pool passes `msg.sender` (the router) as `sender` to the extension:**

In `MetricOmmPool.swap`, `_beforeSwap` is called with `msg.sender` as the first argument: [1](#0-0) 

When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the router address is what gets forwarded to the extension as `sender`.

**Extension checks the router address, not the end user:**

`SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router: [2](#0-1) 

**Router stores the real user only in transient callback context for payment, never forwarding it to the pool:**

In `exactInputSingle`, the actual caller (`msg.sender`) is stored only for the payment callback via `_setNextCallbackContext`. The `pool.swap` call passes no user identity: [3](#0-2) 

**Two broken outcomes:**
1. **Allowlist bypass**: Admin allowlists the router (the only way to let any user swap through it). Every address — including those never allowlisted — can call `router.exactInputSingle` and pass the check because the extension sees `sender = router` (allowlisted).
2. **Allowlisted users locked out**: Admin allowlists individual user addresses. Those users cannot swap through the router because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`.

`DepositAllowlistExtension` does not share this bug because it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`: [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a permissioned institutional pool) is fully bypassed by any unprivileged address routing through `MetricOmmSimpleRouter`. Real token flows occur — the attacker receives pool output tokens and the pool receives input tokens. The allowlist guard, the only access-control mechanism on the swap path, is rendered completely ineffective. This is a broken core pool functionality and an admin-boundary break: an unprivileged path defeats a pool-admin-configured guard.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user who discovers the allowlist can trivially route through the router with a single `exactInputSingle` call. No special privileges, flash loans, or multi-step setup are required. The pool admin has no on-chain mechanism to prevent this without removing the router from the allowlist entirely (which defeats the purpose of enabling router-mediated swaps).

## Recommendation
Forward the real user identity through the swap path. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This is opt-in per deployment and requires no core interface change.
2. **Add a `realSender` field to the pool's `swap` interface**: The pool accepts an explicit caller identity parameter and passes it to extensions. This is the cleanest fix but requires a core interface change.

## Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap through it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (never individually allowlisted) calls the router directly.
// Extension sees sender = router (allowlisted) → passes.
// Attacker receives pool output tokens.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds. Allowlist completely bypassed.
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
