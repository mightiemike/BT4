Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Real User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. If a pool admin allowlists the router to enable router-mediated swaps for approved users, every unprivileged user can bypass the allowlist by calling the same public router. There is no configuration that simultaneously permits router-mediated swaps for approved users while blocking unapproved users.

## Finding Description
`SwapAllowlistExtension.beforeSwap` enforces access control as:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension caller) and `sender` is the first argument forwarded by `MetricOmmPool.swap`, which passes `msg.sender` (the pool's caller) directly:

```solidity
_beforeSwap(
    msg.sender,   // pool's msg.sender — whoever called pool.swap()
    recipient, ...
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly, making the router the pool's `msg.sender`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
``` [3](#0-2) 

The extension receives `sender = router` and checks `allowedSwapper[pool][router]`. The `bytes calldata` (extensionData) parameter in `beforeSwap` is unnamed and entirely ignored — there is no mechanism to recover the real user identity. [4](#0-3) 

The `DepositAllowlistExtension` avoids this problem by gating on `owner` (the position recipient, a user-controlled identity) rather than `sender` (the payer/caller), but no equivalent identity-resolution exists for swaps. [5](#0-4) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` for regulatory compliance, institutional-only access, or protocol-controlled liquidity has its access control completely bypassed. Any unprivileged user who calls `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` against such a pool — provided the router is allowlisted — executes swaps the allowlist was designed to prevent. This breaks the core pool access invariant and allows unauthorized parties to interact with restricted liquidity, constituting broken core pool functionality with direct fund-impact potential.

## Likelihood Explanation
The scenario requires the pool admin to have allowlisted the router address — a natural and expected operational step for any admin who wants approved users to benefit from the router's slippage protection, deadline checks, and multi-hop routing. Once the router is allowlisted, the bypass is reachable by any unprivileged user with no special setup beyond calling the public router. The condition is realistic and the exploit is trivially repeatable.

## Recommendation
The extension must resolve the actual end-user identity rather than the intermediary caller. Two viable approaches:

1. **Decode user from `extensionData`**: Have the router encode `msg.sender` (the real user) into `extensionData` for each hop using a standard prefix or ABI encoding, and have `SwapAllowlistExtension.beforeSwap` decode and verify that address instead of `sender`. The router must be the one encoding this (not the user) to prevent spoofing.

2. **Reject router-mediated calls unless real user is recoverable**: Add a convention that extensions can use to identify the true originator when an intermediary is involved, and revert if the convention is absent.

The `DepositAllowlistExtension` correctly gates on `owner` (the position recipient) rather than `sender` (the payer) — the swap allowlist needs an equivalent identity-resolution step.

## Proof of Concept
```
Setup:
  1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     to enable router-mediated swaps for approved users.
  3. Pool admin does NOT call setAllowedToSwap(pool, alice, true)
     (alice is not an approved direct swapper).

Attack:
  4. Alice calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  5. Router calls pool.swap(...) — pool's msg.sender = router.
  6. Pool calls _beforeSwap(router, ...).
  7. Extension checks allowedSwapper[pool][router] == true → passes.
  8. Alice's swap executes successfully despite not being on the allowlist.

Result:
  Alice, an unprivileged user, swaps in a pool that was supposed to
  restrict access to approved counterparties only.

Foundry test outline:
  - deployPool(SwapAllowlistExtension)
  - vm.prank(admin); ext.setAllowedToSwap(pool, router, true)
  - vm.prank(alice); router.exactInputSingle(...)
  - assertEq(swap succeeded, true)  // bypass confirmed
```

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
