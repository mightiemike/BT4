Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any non-allowlisted address to bypass the deposit gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (unnamed first parameter) and gates on `owner` instead. Because `MetricOmmPool.addLiquidity` permits `msg.sender != owner`, any non-allowlisted address can call `addLiquidity(owner=allowlisted_address)` and the allowlist check passes unconditionally, rendering the deposit gate inoperative.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` that is fully independent of `msg.sender`: [1](#0-0) 

It passes both `msg.sender` (the actual depositor/payer) and `owner` (the LP position recipient) into the hook dispatcher at line 191. The interface correctly defines both as named parameters: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` entirely (unnamed first parameter) and only checks `owner`: [3](#0-2) 

The check `allowedDepositor[msg.sender][owner]` uses `msg.sender` as the pool key (correct — the pool calls the extension) but `owner` as the depositor identity (wrong — `owner` is the LP share recipient, not the token payer). The actual token payer is `sender`, which is silently ignored.

Token payment is collected from the pool's `msg.sender` (the attacker) via the modify-liquidity callback: [4](#0-3) 

LP shares are credited to the position key derived from `owner` (the allowlisted victim): [5](#0-4) 

The contract's stated purpose is to gate `addLiquidity` by depositor address: [6](#0-5) 

But the actual depositor (`sender`/payer) is never checked. No existing guard prevents `msg.sender != owner` in `addLiquidity` (contrast with `removeLiquidity` at line 206 which enforces `msg.sender == owner`): [7](#0-6) 

## Impact Explanation

The deposit allowlist is completely bypassed. Any non-allowlisted address can call `pool.addLiquidity(owner=any_allowlisted_address, ...)`, pass the `beforeAddLiquidity` check because `allowedDepositor[pool][allowlisted_address] = true`, pay tokens via the callback from their own balance, and mint LP shares into the allowlisted address's position without their consent. This breaks the core access-control invariant the extension is designed to enforce and constitutes broken core pool functionality causing direct manipulation of bin token balances and LP position accounting.

## Likelihood Explanation

The entrypoint is the public `addLiquidity` function with no privilege requirement. The `owner` parameter is fully attacker-controlled. No special setup, oracle manipulation, or trusted role is needed. Any EOA or contract can execute this with a single transaction, and the attack is repeatable indefinitely.

## Recommendation

Replace the unnamed `address` (sender) with a named parameter and gate on `sender` instead of `owner`:

```solidity
// Before (broken):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
```

This ensures the actual token payer — not the LP share recipient — is the identity checked against the allowlist, matching the contract's stated purpose.

## Proof of Concept

```solidity
function test_nonAllowlistedAttackerBypassesDepositAllowlist() public {
    address victim   = makeAddr("victim");
    address attacker = makeAddr("attacker");

    // Only victim is allowlisted
    depositExtension.setAllowedToDeposit(address(pool), victim, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Fund attacker and approve pool
    token0.mint(attacker, 1_000_000e18);
    token1.mint(attacker, 1_000_000e18);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner=victim — should revert but does NOT
    LiquidityDelta memory delta = _createDeltaArray(0, 10_000);
    pool.addLiquidity(victim, 0, delta, "", "");
    vm.stopPrank();

    // allowedDepositor[pool][attacker] was false throughout, yet deposit succeeded
    // because the check evaluated allowedDepositor[pool][victim] instead
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));
}
```

`allowedDepositor[pool][attacker]` remains `false` throughout, yet the deposit succeeds because `beforeAddLiquidity` evaluated `allowedDepositor[pool][victim]` instead of `allowedDepositor[pool][attacker]`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L182-191)
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
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-11)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L121-121)
```text
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```
