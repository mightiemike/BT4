Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of Actual Swapper, Enabling Full Per-User Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which resolves to `msg.sender` of the pool's `swap()` call — the direct caller (e.g., `MetricOmmSimpleRouter`), not the actual end user. When a pool admin allowlists the router to permit authorized users to trade, every user who routes through that contract bypasses the per-user restriction, because the allowlist check resolves to the single router address. This collapses a per-user guard into a per-router guard, allowing unauthorized users to execute swaps in pools intended to be private.

## Finding Description

`SwapAllowlistExtension.beforeSwap` checks `sender` against `allowedSwapper[msg.sender][sender]`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-165
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)
)
```

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)`, the pool's `msg.sender` is the router, so `sender = router`. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks the actual beneficiary `owner` (the LP position owner), not the intermediary `sender`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The two sibling extensions apply fundamentally different identity models; the swap extension uses the wrong one.

## Impact Explanation

A pool admin who deploys a restricted pool (e.g., a private institutional pool with tight spreads) and allowlists `MetricOmmSimpleRouter` to allow authorized users to trade through it inadvertently grants access to every user who calls the router. Unauthorized users can execute swaps in a pool intended to be private, and if the pool offers subsidized or favorable pricing (tight bid/ask spread), they can drain LP value at below-market rates. LP principal is exposed to unauthorized counterparties at prices the LPs did not intend to offer to the general public. This constitutes a direct loss of user principal and broken core pool access-control functionality.

## Likelihood Explanation

The trigger path requires no privileged access. Any user can call `MetricOmmSimpleRouter` with any pool address. The pool admin's natural action of allowlisting the router (to enable authorized users to trade) is the exact precondition that opens the bypass. The divergence between intent and outcome is silent — no on-chain warning is emitted. The scenario is realistic for any institutional or permissioned pool deployment.

## Recommendation

Mirror the `DepositAllowlistExtension` pattern: check the actual swap beneficiary, not the intermediary. The `beforeSwap` interface already receives `recipient` as the second argument (currently ignored with `address`). The extension should check `recipient` instead of `sender`, or the pool should pass a dedicated `swapper` field representing the true initiator (analogous to `owner` in the deposit path). At minimum, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps for per-user access control.

## Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension in BEFORE_SWAP_ORDER.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (natural action: allow authorized users to trade via MetricOmmSimpleRouter).
  - Unauthorized user (not individually allowlisted) calls:
      MetricOmmSimpleRouter.exactInputSingle({pool: pool, recipient: unauthorizedUser, ...})
        → pool.swap(recipient=unauthorizedUser, ...)   [msg.sender = router]
          → _beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes)
          → swap executes for unauthorized user

Result:
  - allowedSwapper[pool][unauthorizedUser] == false (never set)
  - Guard passed because sender == router, which IS allowlisted.
  - Unauthorized user receives swap output; pool LPs bear counterparty risk
    they intended to restrict.

Foundry test outline:
  1. Deploy pool with SwapAllowlistExtension.
  2. Call setAllowedToSwap(pool, address(router), true).
  3. Prank as unauthorizedUser, call router.exactInputSingle(...).
  4. Assert swap succeeds despite unauthorizedUser not being in allowedSwapper.
  5. Assert allowedSwapper[pool][unauthorizedUser] == false.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
