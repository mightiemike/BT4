Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()`. When users swap through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist check resolves to `allowedSwapper[pool][router]`. Any pool admin who allowlists the router to enable router-mediated swaps for legitimate users inadvertently grants every user — including non-allowlisted ones — the ability to bypass the guard.

## Finding Description
`SwapAllowlistExtension.beforeSwap` performs the allowlist check as follows:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol lines 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the only caller of this hook) and `sender` is the first argument forwarded by `MetricOmmPool.swap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol lines 230-240
_beforeSwap(
    msg.sender,   // whoever called pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol lines 72-80
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

So `msg.sender` from the pool's perspective is the router address, making `sender` in `beforeSwap` the router — not the originating EOA. The allowlist lookup becomes `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the natural step to make the pool usable via the standard periphery), every user can bypass the guard by routing through the same router contract. There is no configuration that simultaneously allows router-mediated swaps for allowlisted users while blocking non-allowlisted users. [1](#0-0) [2](#0-1) [3](#0-2) 

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router to enable legitimate router-mediated swaps inadvertently opens the pool to all users. Any non-allowlisted user can trade on the pool by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. This breaks the core invariant of the allowlist guard and exposes LP funds to unauthorized counterparties who may exploit stale oracle prices, trigger stop-loss conditions, or drain liquidity through arbitrage — constituting a direct loss of LP principal above contest thresholds.

## Likelihood Explanation
The trigger is fully unprivileged: any user who observes that a pool has a `SwapAllowlistExtension` and that the router is allowlisted can immediately bypass the guard. The pool admin's natural operational step — allowlisting the router so that legitimate users can interact via the standard periphery — is precisely the action that opens the bypass. No special knowledge, flash loan, or privileged access is required. The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in `MetricOmmSimpleRouter`.

## Recommendation
The `SwapAllowlistExtension` must gate the **originating user**, not the immediate caller of `pool.swap()`. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`; the extension decodes and checks that address. This requires a coordinated change between the router and the extension, and must validate that the attested address cannot be forged by an arbitrary caller.

2. **Trusted router attestation**: The extension reads a trusted field from `extensionData` when `sender` is a known router address, falling back to `sender` for direct calls. The set of trusted routers must be admin-controlled and validated.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension
  pool admin calls: setAllowedToSwap(pool, alice, true)
  pool admin calls: setAllowedToSwap(pool, router, true)  ← to enable router swaps for alice

Attack:
  bob (not allowlisted) calls:
    router.exactInputSingle({pool: pool, ...})

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient, zeroForOne, amount, limit, "", extensionData)
        msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            allowedSwapper[pool][router] == true  ← router is allowlisted
            → returns selector (no revert)
      → swap executes
      → bob receives output tokens

Result: bob trades on a curated pool without being on the allowlist.
        LP funds are exposed to an unauthorized counterparty.
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
