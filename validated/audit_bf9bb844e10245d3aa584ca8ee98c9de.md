Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument and checks only `owner`. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` parameter with no `msg.sender == owner` guard, any unprivileged caller can pass an allowlisted address as `owner` and have the extension approve the call, fully bypassing the access control mechanism the pool admin configured.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

The extension's `beforeAddLiquidity` hook signature names the first parameter unnamed (discarded) and only inspects `owner`: [2](#0-1) 

The check on line 38 is `allowedDepositor[msg.sender][owner]` — `msg.sender` here is the pool (correct for the pool-key lookup), but `owner` is the position recipient, not the actual depositor. The real depositor (`sender`) is never consulted.

By contrast, `removeLiquidity` enforces `msg.sender == owner`: [3](#0-2) 

No equivalent guard exists in `addLiquidity`. The `addLiquidity` function accepts any caller-supplied `owner` without restriction: [4](#0-3) 

The exploit path is: attacker calls `pool.addLiquidity(owner = allowlisted_address, ...)` → pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)` → extension checks `allowedDepositor[pool][alice] == true` → call is approved → attacker provides tokens via callback → position is credited to `alice`.

## Impact Explanation

The deposit allowlist — the sole access-control mechanism the pool admin configured — is fully bypassed for the actual depositing party. Unauthorized addresses can interact with a restricted pool, violating any compliance, KYC, or partner-gating intent encoded in the allowlist. This constitutes an admin-boundary break: an unprivileged path circumvents a pool admin-configured access control. The allowlisted `owner` receives an unsolicited position they did not initiate; smart-contract owners that do not expect inbound positions may be unable to handle them correctly. Because `removeLiquidity` requires `msg.sender == owner`, the attacker cannot reclaim the deposited tokens, but the pool's bin state and price impact are altered by the unauthorized deposit, potentially harming existing LPs.

## Likelihood Explanation

The attack requires no privileged access, no special token behavior, and no off-chain coordination. Any EOA or contract can call `addLiquidity` with an allowlisted `owner`. The allowlisted address is discoverable on-chain via the public `allowedDepositor` mapping: [5](#0-4) 

Likelihood is high whenever a pool is deployed with `DepositAllowlistExtension` and a non-empty allowlist.

## Recommendation

Replace the `owner` check with a `sender` check in `beforeAddLiquidity`:

```solidity
// fixed
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
  external
  view
  override
  returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This aligns with the NatDoc ("Gates `addLiquidity` by depositor address") and with the `isAllowedToDeposit` / `setAllowedToDeposit` API, which takes a `depositor` parameter intended to represent the calling party: [6](#0-5) 

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

function test_unauthorizedOperatorBypassesAllowlist() public {
    address alice    = makeAddr("alice");
    address attacker = makeAddr("attacker");

    // Pool admin allowlists only alice
    depositExtension.setAllowedToDeposit(address(pool), alice, true);
    assertFalse(depositExtension.isAllowedToDeposit(address(pool), attacker));

    // Fund attacker and approve pool
    token0.mint(attacker, 1_000_000);
    token1.mint(attacker, 1_000_000);
    vm.startPrank(attacker);
    token0.approve(address(pool), type(uint256).max);
    token1.approve(address(pool), type(uint256).max);

    // Attacker deposits on behalf of alice — extension checks owner (alice), not sender (attacker)
    LiquidityDelta memory delta = _buildDelta(0, 10_000);
    pool.addLiquidity(alice, SALT, delta, "", "");  // succeeds — should revert
    vm.stopPrank();

    // Alice now owns a position she never requested; attacker bypassed the allowlist
    uint256 shares = _getShares(address(pool), alice, SALT, 0);
    assertGt(shares, 0, "attacker deposited on behalf of allowlisted owner");
}
```

The test passes (no revert) because `beforeAddLiquidity` approves the call based on `alice`'s allowlist status, never inspecting `attacker` as the actual depositor. [2](#0-1)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-29)
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
