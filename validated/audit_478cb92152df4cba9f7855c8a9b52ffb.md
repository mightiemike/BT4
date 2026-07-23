Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on position `owner` instead of transaction `sender`, allowing complete allowlist bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks `owner` (the caller-supplied position recipient) against the allowlist. Because `addLiquidity` imposes no restriction on who may be named as `owner`, any unprivileged address can bypass the deposit allowlist entirely by supplying any allowlisted address as the `owner` parameter.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to `_beforeAddLiquidity`:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes this as `(sender, owner, ...)` and dispatches to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives both values but leaves the first parameter (`sender`) unnamed and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The allowlist management functions (`setAllowedToDeposit`, `setAllowAllDepositors`, `isAllowedToDeposit`) all operate on the `depositor` dimension, confirming the design intent is to gate the actual depositing address: [4](#0-3) 

Since `addLiquidity` places no restriction on the caller-supplied `owner` value, any address can pass an allowlisted address as `owner` and the guard evaluates `allowedDepositor[pool][alice] == true` and returns success — without ever inspecting the actual depositor.

## Impact Explanation

The deposit allowlist is the pool admin's primary mechanism for restricting which addresses may provide liquidity (e.g., KYC/AML compliance, institutional-only pools). With this bug the guard is completely inoperative: any unprivileged address deposits freely by naming any allowlisted address as `owner`. The allowlisted address receives an unsolicited LP position it did not authorise, exposing it to pool risk (impermanent loss, fee exposure, oracle-driven value changes) without consent. This satisfies the **admin-boundary break** impact criterion: the pool admin's configured access boundary is bypassed by a zero-privilege path.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a publicly observable allowlisted address as `owner`. No special role, flash loan, oracle manipulation, or privileged access is needed. Any address can execute this at any time the pool is active, making exploitation trivially repeatable.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on it instead of `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt, deltas, callbackData, extensionData)`.
3. The pool calls `_beforeAddLiquidity(sender = bob, owner = alice, ...)`.
4. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true`; the guard passes without inspecting Bob.
5. `LiquidityLib.addLiquidity` records the position under `alice`; the liquidity callback charges tokens from Bob's address.
6. Bob has deposited into a pool he is not authorised to enter. Alice holds an LP position she never requested, bearing all associated pool risk (impermanent loss, fee exposure).

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-30)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
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
