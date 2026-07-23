Audit Report

## Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the end-user, allowing any caller to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, the router becomes `msg.sender` at the pool level. If the pool admin allowlists the router (required for any router-mediated swap to succeed), the allowlist check passes for every caller regardless of their individual allowlist status, fully bypassing the access control.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` at the pool level: [3](#0-2) 

The pool admin faces an inescapable dilemma: if the router is not allowlisted, no user can swap through the router at all. If the router is allowlisted (`allowedSwapper[pool][router] == true`), the check passes for every caller regardless of their individual allowlist status. The extension never sees the original end-user address. By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the economic beneficiary) rather than `sender` (the caller): [4](#0-3) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties is fully circumvented. Any unpermissioned address calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`/`exactOutputSingle`/`exactOutput`) targeting the pool. The extension sees `sender == router`, which is allowlisted, and returns success. The unauthorized user executes swaps against LP reserves at oracle-derived prices. This constitutes direct loss of LP principal on pools whose entire value proposition is access control — a critical/high impact under the allowed impact gate (broken core pool functionality causing loss of funds).

## Likelihood Explanation

`MetricOmmSimpleRouter` is the canonical public swap entrypoint. Any user aware of the allowlist restriction has an obvious incentive to route through it. The bypass requires no special privileges, no flash loans, and no multi-transaction setup — a single `exactInputSingle` call suffices. Likelihood is high whenever a pool admin allowlists the router to support normal user flows, which is the only way to permit router-mediated swaps at all.

## Recommendation

The extension must check the original end-user, not the intermediate caller. The preferred fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router. Alternatively, the pool admin must never allowlist the router, but this breaks all router-mediated swaps for allowlisted users and must be explicitly documented and enforced at the factory level. The `DepositAllowlistExtension` pattern — checking the economic beneficiary (`owner`) rather than the caller (`sender`) — is the correct model to follow.

## Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension configured.
  2. Pool admin calls setAllowedToSwap(pool, router, true)
     (required so that allowlisted users can use the router).
  3. Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  4. attacker calls MetricOmmSimpleRouter.exactInputSingle({
         pool: pool,
         recipient: attacker,
         zeroForOne: true,
         amountIn: X,
         ...
     });

  5. Router calls pool.swap(attacker_recipient, ...) — msg.sender at pool = router.
  6. Pool calls _beforeSwap(router, attacker_recipient, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  8. Swap executes. Attacker receives tokens from LP reserves.

Result:
  allowedSwapper[pool][attacker] was never set, yet the swap succeeds.
  The allowlist guard is fully bypassed.
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
