Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position `owner` Instead of Actual Depositor, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` ignores its `sender` parameter and gates access by checking `owner` (the position holder) against the allowlist. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-supplied `owner` with no requirement that it equals `msg.sender`, any unprivileged actor can bypass the deposit allowlist by naming any allowlisted address as `owner` while paying the tokens themselves.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and checks only `owner`:

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

The pool passes `msg.sender` (the LiquidityAdder) as `sender` and the caller-supplied `owner` as `owner` when invoking the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` from the caller and only validates it is non-zero:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(
    address pool, address owner, uint80 salt, LiquidityDelta calldata deltas,
    uint256 maxAmountToken0, uint256 maxAmountToken1, bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L247-249
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
```

The actual payer is always `msg.sender` of `addLiquidityExactShares`, stored separately in transient context via `_setPayContext(pool, payer, ...)` at line 193. There is no requirement that `owner == msg.sender`. The extension therefore checks the wrong address: it checks the position recipient (`owner`) rather than the token payer (the actual depositor).

## Impact Explanation
An unprivileged actor (Bob, not on the allowlist) can add liquidity to a pool protected by `DepositAllowlistExtension` by naming any allowlisted address (Alice) as `owner`. The extension checks `allowedDepositor[pool][alice]` → `true` → no revert. Bob pays the tokens; Alice receives the LP shares. The pool admin's deposit restriction — intended to enforce KYC/compliance gating — is fully and deterministically bypassed. This constitutes a broken core pool functionality (admin-boundary break): the deposit allowlist guard is reachable but misapplied, producing a state the pool admin explicitly intended to prevent.

## Likelihood Explanation
No special privileges are required. Any EOA or contract can call `addLiquidityExactShares` with an arbitrary `owner`. Allowlisted addresses are publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The LiquidityAdder is a standard periphery contract intended for general use. The bypass is deterministic and requires no timing, oracle manipulation, or flash loans.

## Recommendation
`DepositAllowlistExtension.beforeAddLiquidity` should check the actual depositing actor rather than the position holder. Two options:

1. **Check `sender` instead of `owner`**: Replace `allowedDepositor[msg.sender][owner]` with `allowedDepositor[msg.sender][sender]`. Pool admins allowlist the LiquidityAdder for router-mediated deposits and individual EOAs for direct deposits.

2. **Enforce `owner == msg.sender` in `addLiquidityExactShares`**: Restrict the explicit-owner overload so the caller can only deposit into their own position, making `sender == owner` always true for the LiquidityAdder path. This is simpler and closes the bypass without changing extension semantics.

## Proof of Concept
```
Setup:
  - Pool deployed with DepositAllowlistExtension
  - allowedDepositor[pool][alice] = true
  - Bob is NOT allowlisted

Attack:
  Bob calls:
    LiquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")

Execution trace:
  1. _validateOwner(alice)  → passes (alice != address(0))
  2. _addLiquidity(pool, alice, salt, deltas, msg.sender=Bob, ...)
  3. _setPayContext(pool, Bob, max0, max1)
  4. pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), "")
       msg.sender in pool = LiquidityAdder
  5. _beforeAddLiquidity(LiquidityAdder, alice, ...)
  6. DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, alice, ...)
       allowedDepositor[pool][alice] == true  → NO REVERT
  7. LiquidityLib.addLiquidity mints shares to alice
  8. Callback pulls tokens from Bob (payer stored in transient context)

Result:
  Bob (unauthorized) has added liquidity to the restricted pool.
  Alice holds the LP shares; Bob paid the tokens.
  The deposit allowlist is bypassed.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-196)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
