Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the router — not the originating user. When a pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged address can bypass the allowlist by routing through the public `MetricOmmSimpleRouter`. This completely defeats the access control the pool admin intended to enforce.

## Finding Description
In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks: [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the router (wrong). When `charlie` calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly with no forwarding of the original caller: [4](#0-3) 

The router stores `msg.sender` only in transient callback context for payment purposes (`_setNextCallbackContext(..., msg.sender, ...)`), but never passes it to the pool as the originating swapper. The pool therefore receives `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][charlie]`.

**Two broken outcomes:**
- If admin allowlists the router (required for any user to swap via router): every address, including blocked ones, can bypass the allowlist.
- If admin does not allowlist the router: legitimate allowlisted users cannot use the router at all.

The `DepositAllowlistExtension` has the same structural pattern but is not affected because `addLiquidity` checks the `owner` parameter (which the caller explicitly sets to themselves), not `sender`. [5](#0-4) 

## Impact Explanation
Any unprivileged address can trade on a curated/restricted pool by routing through the public `MetricOmmSimpleRouter`. The pool admin's allowlist — intended to enforce KYC, institutional access, or regulatory restrictions — is completely defeated. LP liquidity on the restricted pool is exposed to all traders, not just the intended set. This is a direct admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses a configured access control that the pool admin believed was enforced.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical swap entrypoint for end users. Any pool admin who wants allowlisted users to use the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially reachable by any address calling `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. No special privileges, no setup, no flash loans required — a single public function call suffices.

## Recommendation
The extension must check the original user, not the immediate pool caller. The router already stores `msg.sender` in transient context via `_setNextCallbackContext`. The fix requires threading the original caller through to the extension, either:
1. Encoding the original caller in `extensionData` at the router level and reading it in the extension.
2. Having the pool forward the original caller as a dedicated parameter rather than its own `msg.sender`.

The invariant that must hold: `allowedSwapper[pool][X]` must be checked against the address that economically controls the swap (the EOA or contract that initiated the transaction), not the intermediate router.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only alice and router are allowlisted
// (admin must allowlist router to let alice use it via the canonical UX path)
extension.setAllowedToSwap(pool, alice, true);
extension.setAllowedToSwap(pool, address(router), true);

// Attack: charlie (not allowlisted) bypasses the allowlist via the router
vm.prank(charlie);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: charlie,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Succeeds: pool sees sender=router, allowedSwapper[pool][router]=true
// charlie trades on a pool he was never supposed to access
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-39)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
```
