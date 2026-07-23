Audit Report

## Title
`SwapAllowlistExtension` Checks Router Address Instead of Real User, Enabling Allowlist Bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is always `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract address. If the pool admin allowlists the router to enable standard periphery access for their curated users, every unprivileged user can bypass the allowlist by routing through the router, as the extension only sees the router address and not the real user.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` checks that argument against the per-pool allowlist using `msg.sender` (the pool) as the key:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly without forwarding the original user's address in any verifiable way:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

When a user calls `exactInputSingle`, the pool sees `msg.sender = router`. The extension then evaluates `allowedSwapper[pool][router]`. The extension does not inspect `extensionData` at all — it only checks `sender`. This creates an impossible configuration for pool admins:

- **Do not allowlist the router** → allowlisted users cannot use the standard periphery.
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through the router.

No configuration simultaneously permits allowlisted users to use the router and blocks non-allowlisted users.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or protocol-controlled accounts) is fully bypassed. Any unprivileged user can execute swaps against the pool's liquidity by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This exposes LP funds to unrestricted market participants, directly contradicting the pool's configured access policy. This constitutes a broken core pool access-control mechanism causing potential LP principal loss if the pool was sized for a controlled trading environment — matching the "admin-boundary break" and "broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
The router is the canonical, documented periphery entry point. Pool admins who want their allowlisted users to have a normal UX will allowlist the router as a matter of course. The bypass requires no special privileges, no flash loans, and no unusual token behavior — any EOA can call `exactInputSingle` on the router pointing at the curated pool. The attack is repeatable and costless beyond gas.

## Recommendation
The pool must forward the original user's identity through the call stack. The most robust fix is for `SwapAllowlistExtension.beforeSwap` to decode the real user address from `extensionData` when `sender` is a known trusted router, and check that decoded address against the allowlist. The router must be updated to encode `msg.sender` into `extensionData` before calling `pool.swap()`. Alternatively, the extension can maintain a registry of trusted routers and require that for trusted routers, `extensionData` contains a verified user address. Without such a mechanism, the allowlist invariant cannot be upheld when the router is used.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to let their allowlisted users use the standard periphery)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  - attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) → msg.sender = router
  - Pool calls _beforeSwap(router, ...)
  - SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  - Swap executes for attacker despite attacker not being on the allowlist
```

The attacker pays nothing extra and needs no special setup beyond calling the public router. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` since all router entry points call `pool.swap()` with `msg.sender = router`. [1](#0-0) [2](#0-1) [3](#0-2)

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
