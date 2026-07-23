Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Allowlist Bypass or DoS — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument forwarded by the pool, which is the pool's `msg.sender` — the router contract — not the originating user. When users swap via `MetricOmmSimpleRouter`, the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. This produces either a full allowlist bypass (if the admin allowlists the router) or a DoS on all allowlisted users (if the admin does not).

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on that forwarded `sender`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly with no mechanism to encode the originating `msg.sender` into `extensionData` or any other field: [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The call chain is:

```
user → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← wrong actor
```

`DepositAllowlistExtension` does not share this flaw because it gates on `owner`, which `MetricOmmPoolLiquidityAdder` correctly passes as `msg.sender` of the adder call: [4](#0-3) [5](#0-4) 

## Impact Explanation

**Mode A — Allowlist bypass (higher severity):** If the pool admin allowlists the router address (the intuitive fix to allow allowlisted users to use the standard periphery path), the router becomes a public bypass vector. Any unprivileged user — including those explicitly excluded — can call `exactInputSingle` and trade in the curated pool. Per-user curation is completely defeated, exposing LP funds to unauthorized actors (unauthorized price impact, fee extraction, or compliance violation on regulated pools). This is a broken admin-boundary / broken core pool access-control impact.

**Mode B — DoS on legitimate users:** If the admin does not allowlist the router, every allowlisted user who calls the router is rejected with `NotAllowedToSwap`. The only usable path is a direct `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` — not feasible for ordinary EOAs. This is broken core swap functionality for the intended user set.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool deploying `SwapAllowlistExtension` and expecting users to interact through the router immediately encounters one of the two failure modes. Mode B is triggered on first use; Mode A is triggered the moment the admin allowlists the router to resolve Mode B, which is the natural remediation step. No special privileges are required — any public caller can trigger the bypass.

## Recommendation

Have the router encode `msg.sender` into `extensionData` before calling `pool.swap`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when present, falling back to `sender` for direct pool calls. Alternatively, add a dedicated `originalSender` field to the `beforeSwap` hook arguments, or require that allowlisted pools be interacted with directly (bypassing the router). Using `tx.origin` is a last resort and requires explicit trust-assumption documentation.

## Proof of Concept

```solidity
// Pool configured with SwapAllowlistExtension.
// Admin allowlists alice but NOT the router.
extension.setAllowedToSwap(pool, alice, true);

// Alice calls the router — reverts NotAllowedToSwap (Mode B):
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool, tokenIn: token0, recipient: alice,
    zeroForOne: true, amountIn: 1000, amountOutMinimum: 0,
    priceLimitX64: 0, deadline: block.timestamp, extensionData: ""
}));
// Extension checked allowedSwapper[pool][router] == false → revert.

// Admin allowlists the router to "fix" alice's access:
extension.setAllowedToSwap(pool, address(router), true);

// Bob (not allowlisted) now bypasses the allowlist (Mode A):
vm.prank(bob);
router.exactInputSingle(...); // succeeds — allowlist fully bypassed
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L78-81)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
