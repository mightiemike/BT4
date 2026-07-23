Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unauthorized Depositor to Bypass the Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and validates only `owner`, which is a free caller-supplied parameter. Because any caller can nominate an allowlisted address as `owner`, the deposit allowlist is completely bypassed. Any unauthorized address can deposit into a restricted pool in a single transaction.

## Finding Description

`MetricOmmPool.addLiquidity` passes two distinct identities to the hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

- `msg.sender` (first arg) — the address that called `addLiquidity` and will be charged tokens via the liquidity callback.
- `owner` (second arg) — a caller-supplied address that will own the resulting position shares.

`DepositAllowlistExtension.beforeAddLiquidity` unnamed-discards the first parameter and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

The mapping key `depositor` in `allowedDepositor[pool][depositor]` and the NatSpec "Gates `addLiquidity` by depositor address" confirm the intended subject is the paying caller, not the position beneficiary: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly uses `sender` (first arg) for its check, confirming the pattern mismatch:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

## Impact Explanation

Any actor not on the allowlist can deposit into a pool configured to restrict deposits by passing an allowlisted address as `owner`. The extension checks `allowedDepositor[pool][allowlisted_address]` → passes. The unauthorized caller's tokens are pulled via the liquidity callback; the allowlisted address receives position shares it did not request. This renders the deposit allowlist completely inoperative, breaking the admin-boundary access control that pool admins rely on for regulatory or permissioned-pool purposes. Unauthorized actors can also force-create positions in allowlisted users' names without consent.

## Likelihood Explanation

Exploitation requires only: (1) knowledge of one allowlisted address, which is publicly readable from `allowedDepositor`, and (2) the ability to call `pool.addLiquidity`. No special privileges, flash loans, or oracle manipulation are needed. The attack executes in a single transaction and is repeatable indefinitely.

## Recommendation

Replace the unnamed first parameter with `sender` and validate it instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT allowlisted

Attack:
  pool.addLiquidity(
      alice,        // owner — allowlisted, passes the guard
      salt,
      deltas,
      callbackData, // callback pulls tokens from bob (msg.sender)
      extensionData
  );

Result:
  - Extension checks allowedDepositor[pool][alice] → true → no revert
  - Bob's tokens are pulled via the liquidity callback
  - Alice receives position shares she did not request
  - Bob has deposited into a restricted pool without being on the allowlist
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-41)
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-37)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```
