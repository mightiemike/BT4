Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual token-transferring caller) and checks `owner` (the free caller-supplied position beneficiary) instead. Any unprivileged address can bypass the allowlist by passing any allowlisted address as `owner`, causing the pool admin's access-control configuration to be completely nullified.

## Finding Description
`MetricOmmPool.addLiquidity` calls `_beforeAddLiquidity(msg.sender, owner, ...)` where `msg.sender` is the depositor who must satisfy the token callback, and `owner` is the freely chosen position beneficiary. [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both as positional arguments `(sender, owner, ...)` to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` names the first parameter `_` (unnamed) and checks `owner` instead of `sender`: [3](#0-2) 

Because `owner` is a free caller-supplied parameter with no on-chain constraint tying it to `msg.sender`, any caller can pass any allowlisted address as `owner` and the check `allowedDepositor[msg.sender][owner]` returns `true`, bypassing the guard entirely. The callback in `LiquidityLib.addLiquidity` is issued to `msg.sender` (the actual depositor), so the non-allowlisted caller provides the tokens, while LP shares are minted to `owner`: [4](#0-3) 

`removeLiquidity` enforces `msg.sender == owner`, so only `owner` can withdraw the deposited tokens: [5](#0-4) 

The correct pattern is already used in `SwapAllowlistExtension.beforeSwap`, which correctly checks `sender`: [6](#0-5) 

## Impact Explanation
The deposit allowlist is the pool admin's primary mechanism for creating permissioned liquidity pools (KYC-gated, institutional-only, whitelist-only). The bug renders the guard completely inoperative: any excluded address can deposit tokens into the pool by specifying any allowlisted address as `owner`. The depositor's tokens are locked in the allowlisted address's position with no recourse, as only `owner` can call `removeLiquidity`. This constitutes an admin-boundary break (unprivileged path bypasses pool admin's access-control configuration) and a direct loss of user principal (depositor's tokens are irrecoverably transferred to `owner`'s position).

## Likelihood Explanation
Exploitation requires only: (1) knowledge of one allowlisted address, readable from the public `allowedDepositor` mapping or emitted `AllowedToDepositSet` events; and (2) the ability to call `addLiquidity`. No privileged role, flash loan, or oracle manipulation is needed. Any user of the protocol can trigger this on any pool that deploys `DepositAllowlistExtension` in non-allow-all mode.

## Recommendation
Replace the unnamed first parameter with a named `sender` variable and check it instead of `owner`:

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

This mirrors the correct pattern in `SwapAllowlistExtension.beforeSwap`.

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` and allowlists only `alice`:
   ```solidity
   extension.setAllowedToDeposit(pool, alice, true);
   ```
2. `bob` (not allowlisted) calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
3. Inside `beforeAddLiquidity`: `msg.sender == pool`, `owner == alice`. The check `allowedDepositor[pool][alice]` returns `true` → no revert.
4. `LiquidityLib.addLiquidity` issues the token callback to `bob` (`msg.sender`), transferring `bob`'s tokens into the pool. LP shares are minted to `alice`.
5. `bob` cannot call `removeLiquidity(alice, ...)` because `msg.sender != owner` reverts. Only `alice` can withdraw `bob`'s tokens.
6. The pool admin's deposit restriction was never enforced against `bob`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
