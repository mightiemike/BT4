Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks position `owner` instead of actual caller `sender`, allowing allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first argument (`sender`, the actual caller/payer) and gates only `owner` (the position recipient). Because `MetricOmmPoolLiquidityAdder` accepts an arbitrary `owner` parameter validated only for non-zero, any unprivileged address can bypass the deposit allowlist by supplying an allowlisted address as `owner` while remaining the actual token payer.

## Finding Description

In `DepositAllowlistExtension.beforeAddLiquidity`, the `sender` argument is unnamed and ignored; only `owner` is checked against the allowlist: [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` (the liquidity adder contract) as `sender` and the caller-supplied `owner` as the position recipient: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` and validates only that it is non-zero, then stores `msg.sender` (the actual caller, `bob`) as the `payer` in transient context: [3](#0-2) [4](#0-3) 

The callback then pulls tokens from `payer` (`bob`), not from `owner` (`alice`): [5](#0-4) 

**Exploit path:**
1. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — `alice` is allowlisted.
2. `bob` (not allowlisted) calls `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")`.
3. `_validateOwner(alice)` passes (`alice != address(0)`).
4. Adder calls `pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")`.
5. Pool calls `_beforeAddLiquidity(adder, alice, salt, deltas, "")`.
6. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
7. LP shares minted to `alice`; tokens pulled from `bob` via callback.
8. `bob` has deposited into a restricted pool without being allowlisted.

The same bypass applies to `addLiquidityWeighted` (both probe and paying calls use the caller-supplied `owner`): [6](#0-5) 

## Impact Explanation

The deposit allowlist is bypassed entirely. Any unprivileged address can add liquidity to a pool the admin intended to restrict by routing through the liquidity adder with an allowlisted `owner`. This is a direct admin-boundary break: the pool admin's access-control invariant is circumvented by an unprivileged path requiring no special role.

## Likelihood Explanation

Medium. The attacker only needs to know one allowlisted address (readable on-chain from the public `allowedDepositor` mapping) and be willing to pay tokens (which are minted as LP shares to the allowlisted address). No special privilege is required; any EOA or contract can trigger this path.

## Recommendation

Check `sender` (the actual caller/payer) instead of `owner` in `beforeAddLiquidity`:

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

`sender` is `msg.sender` of the pool's `addLiquidity` call — the entity actually providing tokens — which is the correct identity to gate.

## Proof of Concept

```solidity
// 1. Pool deployed with DepositAllowlistExtension; alice is allowlisted
depositAllowlist.setAllowedToDeposit(pool, alice, true);

// 2. bob (not allowlisted) bypasses the gate via the liquidity adder
liquidityAdder.addLiquidityExactShares(
    pool,
    alice,   // owner = allowlisted address
    salt,
    deltas,
    max0,
    max1,
    ""
);
// Result: extension checks allowedDepositor[pool][alice] == true → passes
// LP shares minted to alice, tokens pulled from bob
// bob has deposited into a restricted pool without being allowlisted
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-114)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
