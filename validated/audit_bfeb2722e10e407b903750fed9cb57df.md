Audit Report

## Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and gates access on `owner` instead. Because `MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address, any non-allowlisted address can bypass the gate by nominating an allowlisted address as `owner`. The attacker pays the tokens via the liquidity callback; the allowlisted address receives the LP position without consent, and the attacker cannot recover the tokens.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (the actual `msg.sender` of the pool call) as its first parameter, but that parameter is unnamed and ignored. The allowlist check is performed only on `owner`: [1](#0-0) 

`MetricOmmPool.addLiquidity` accepts `owner` as a free caller-supplied parameter and passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` and `owner` and forwards them to the extension: [3](#0-2) 

Because the extension ignores `sender` and checks only `allowedDepositor[msg.sender][owner]`, an attacker who supplies an allowlisted address as `owner` will pass the gate unconditionally. After the gate passes, `LiquidityLib.addLiquidity` records the position under `owner` and fires the liquidity callback on `msg.sender` (the attacker) to pull tokens: [4](#0-3) 

The attacker cannot recover the deposited tokens because `removeLiquidity` enforces `msg.sender == owner`: [5](#0-4) 

## Impact Explanation

The deposit allowlist is the pool admin's mechanism to restrict which addresses may add liquidity to a curated pool. The invariant — only allowlisted addresses may act as the depositing party — is broken. Any address can deposit into a restricted pool by nominating an allowlisted address as the position owner. This is a direct admin-boundary break: an unprivileged path bypasses an admin-configured access control. Additionally, the attacker's tokens are permanently transferred to the allowlisted address's position (the attacker loses principal with no recovery path), and the allowlisted address receives LP shares without consent.

## Likelihood Explanation

The bypass requires only a direct call to the public `pool.addLiquidity` function with an allowlisted address as `owner`. No special privileges, flash loans, or complex setup are needed beyond implementing `IMetricOmmModifyLiquidityCallback` (trivial for any contract). Any allowlisted address is publicly discoverable via the `AllowedToDepositSet` event or the `allowedDepositor` view function. The attack is repeatable with no cooldown.

## Recommendation

Change `beforeAddLiquidity` to check `sender` (the actual depositing party) rather than `owner`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

## Proof of Concept

```solidity
// Setup: allowlisted is in the allowlist; attacker is not.
depositExtension.setAllowedToDeposit(address(pool), allowlisted, true);

// Attacker (a contract implementing IMetricOmmModifyLiquidityCallback) calls addLiquidity,
// nominating the allowlisted address as owner.
// Extension checks allowedDepositor[pool][allowlisted] == true → gate passes.
// Attacker's tokens are pulled via callback; LP shares credited to `allowlisted`.
vm.prank(attacker);
pool.addLiquidity(allowlisted, salt, deltas, callbackData, "");

// Assert: attacker's tokens were pulled, allowlisted received shares.
assertGt(positionBinShares(pool, allowlisted, salt, bin), 0);

// Attacker cannot recover: removeLiquidity enforces msg.sender == owner.
vm.prank(attacker);
pool.removeLiquidity(allowlisted, salt, deltas, ""); // reverts with NotPositionOwner
```

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-154)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
```
