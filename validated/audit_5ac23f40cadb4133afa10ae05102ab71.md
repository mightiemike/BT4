Audit Report

## Title
`SwapAllowlistExtension` per-user allowlist fully bypassed when router is allowlisted - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which `MetricOmmPool.swap` populates with `msg.sender` — the direct caller of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` of the pool, so the allowlist checks the router address rather than the originating user. Any pool admin who allowlists the router to support router-mediated swaps for their allowlisted users simultaneously grants unrestricted swap access to every address on-chain.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly without forwarding the originating user:

```solidity
// MetricOmmSimpleRouter.sol L72-80
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

When any user calls `exactInputSingle`, `msg.sender` inside `pool.swap` is the router. The allowlist check therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. A pool admin who sets `allowedSwapper[pool][router] = true` — the natural configuration to let allowlisted users benefit from router features — simultaneously opens the pool to every address that can call the public router. No existing guard in `SwapAllowlistExtension`, `MetricOmmPool`, or `MetricOmmSimpleRouter` recovers or checks the originating user identity.

## Impact Explanation
The swap allowlist is the primary access-control mechanism for restricting which addresses may trade against a pool's liquidity. Bypassing it allows unauthorized users to execute swaps against LP capital in pools intended for private or restricted use (e.g., KYC-gated, partner-only, or institutional pools), directly undermining the core pool functionality of swap access control. This constitutes broken core pool functionality causing potential loss of LP principal, meeting the contest's allowed impact criteria.

## Likelihood Explanation
The bypass requires only that the pool admin has allowlisted the router — a natural and expected configuration for any allowlist-gated pool that also wants to support router-mediated swaps for its permitted users. The attacker needs no special privileges: calling `MetricOmmSimpleRouter.exactInputSingle` is a standard public action available to any address. The router is already deployed and publicly accessible. Any user who discovers the allowlisted router can exploit this immediately and repeatably.

## Recommendation
The `SwapAllowlistExtension` must gate on the originating user, not the intermediary router. Two complementary approaches:

1. **Router-level fix**: `MetricOmmSimpleRouter` should encode `msg.sender` into `extensionData` before calling `pool.swap`, so the extension can decode and check the true swapper identity when the direct `sender` is a recognized router.

2. **Extension-level fix**: `SwapAllowlistExtension.beforeSwap` should decode the originating user from `extensionData` when `sender` is a known router address, or maintain a registry of trusted routers and require the decoded initiator to be allowlisted.

The simplest safe fix is option 1: have the router encode `msg.sender` into `extensionData` and have the extension decode and check that address when the direct `sender` is a recognized router.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension
// Admin allowlists alice and the router
swapAllowlist.setAllowedToSwap(address(pool), alice, true);
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
// bob is NOT allowlisted

// Bob bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: bob,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        tokenIn: token0,
        extensionData: ""
    })
);
// swap succeeds: pool.swap() sees msg.sender = router
// sender = router → allowedSwapper[pool][router] = true → passes
// bob bypassed the per-user allowlist
```

**Call path**: `bob` → `MetricOmmSimpleRouter.exactInputSingle` [1](#0-0)  → `MetricOmmPool.swap` with `msg.sender = router` [2](#0-1)  → `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[pool][router] == true` → passes [3](#0-2)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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
