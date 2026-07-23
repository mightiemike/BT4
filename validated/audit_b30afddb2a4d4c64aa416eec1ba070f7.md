Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks immediate pool caller (`sender`) instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` enforces `allowedSwapper[pool][sender]` where `sender` is `msg.sender` at the time `pool.swap()` is called. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user. Any pool admin who allowlists the router to enable router-based swaps for their users inadvertently grants swap access to every user of the router, bypassing the per-user restriction entirely.

## Finding Description

**Root cause:** `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

**Exploit path:**

In `MetricOmmSimpleRouter.exactInputSingle`, the actual user (`msg.sender`) is stored only in transient storage for the payment callback via `_setNextCallbackContext`. The router itself calls `pool.swap()`: [4](#0-3) 

So the pool receives `msg.sender = router`, and the extension sees `sender = router`. The actual user's address never reaches the extension.

**Preconditions → exploit:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to specific addresses.
2. Admin calls `setAllowedToSwap(pool, router, true)` to allow their allowlisted users to swap via the router (a natural, expected action).
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting that pool.
4. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. The non-allowlisted user successfully swaps, bypassing the per-user restriction.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

**Contrast with `DepositAllowlistExtension`:** The deposit extension checks `owner` (the position beneficiary), which is explicitly propagated through `MetricOmmPoolLiquidityAdder` as the actual user regardless of the immediate caller: [5](#0-4) 

The swap extension has no equivalent mechanism — `sender` is structurally the immediate pool caller, not the economic actor.

## Impact Explanation

Any user can bypass the swap allowlist of a curated pool by routing through `MetricOmmSimpleRouter`, provided the router is allowlisted. This breaks the access control invariant of pools designed for specific institutional counterparties. Unauthorized users gaining swap access exposes LPs to adverse selection at oracle prices and violates the curation guarantee the pool admin configured. This is a direct broken core pool functionality / admin-boundary break impact.

## Likelihood Explanation

Medium. The bypass requires the pool admin to allowlist the router. However, allowlisting the router is the only mechanism available to enable router-based swaps for any user — there is no per-user router delegation path. A pool admin who wants their allowlisted users to use the router will naturally allowlist the router address, inadvertently opening the pool to all router users. The mistake is easy to make and the bypass is repeatable by any address.

## Recommendation

The extension must identify the actual user, not the immediate pool caller. Two concrete options:

1. **Trusted router pattern:** Have `MetricOmmSimpleRouter` encode the actual user (`msg.sender`) into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and verify it when `sender` is a known trusted router. This requires the extension to maintain a registry of trusted routers.
2. **Recipient-based check:** Check `recipient` instead of `sender` if the pool's design guarantees the recipient is the actual user. This is weaker and may not hold for multi-hop paths where intermediate recipients are the router itself.

Option 1 is the more robust fix, as it preserves the intent of per-user access control regardless of routing path.

## Proof of Concept

```solidity
// 1. Deploy pool with SwapAllowlistExtension
// 2. Admin allowlists the router: setAllowedToSwap(pool, router, true)
// 3. Non-allowlisted attacker calls:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: attacker,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    tokenIn: token0,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → passes
// Attacker swaps successfully despite not being individually allowlisted
```

A Foundry integration test can deploy `SwapAllowlistExtension`, configure a pool with it, allowlist only the router, and assert that a non-allowlisted EOA calling `exactInputSingle` succeeds (demonstrating the bypass).

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
