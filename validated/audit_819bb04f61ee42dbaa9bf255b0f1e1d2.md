Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position `owner` Instead of Actual Payer, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and gates access by the `owner` (position holder) argument instead. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` allows any caller to supply an arbitrary non-zero `owner` while paying tokens themselves, an unauthorized actor can bypass the deposit allowlist by naming any allowlisted address as `owner`. The pool admin's intended access restriction is fully circumvented without any special privilege.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` ignores its first `address` parameter (`sender`) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` (the LiquidityAdder) as `sender` and the caller-supplied `owner` as `owner` to the extension:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts an arbitrary `owner` from the caller and only validates it is non-zero:

```solidity
function addLiquidityExactShares(address pool, address owner, ...) external payable override ... {
    _validateOwner(owner);   // only checks owner != address(0)
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
``` [3](#0-2) 

```solidity
function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
}
``` [4](#0-3) 

The actual payer is always `msg.sender` of `addLiquidityExactShares`, stored separately in transient context and used only in the callback to pull tokens: [5](#0-4) 

There is no requirement that `owner == msg.sender`. The extension's own NatSpec states it "Gates `addLiquidity` by depositor address" — meaning the depositing actor — but the implementation gates by position holder (`owner`), which is a different address when the LiquidityAdder is used. [6](#0-5) 

## Impact Explanation

An unauthorized actor (Bob, not on the allowlist) can add liquidity to a restricted pool by naming any allowlisted address (Alice) as `owner`. The extension checks `allowedDepositor[pool][alice]` → `true` → no revert. Bob pays the tokens; Alice receives the LP shares. The pool admin's deposit restriction is fully bypassed. This constitutes a broken core pool access control mechanism: the deposit allowlist guard is reachable but misapplied, producing a state the pool admin explicitly intended to prevent. Unauthorized actors can influence pool composition and bin state, affecting price discovery and LP returns for existing holders.

## Likelihood Explanation

No special privileges are required; any EOA or contract can call `addLiquidityExactShares` with an arbitrary `owner`. Allowlisted addresses are publicly discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`. The LiquidityAdder is a standard periphery contract intended for general use. The bypass is deterministic and requires no timing, oracle manipulation, or flash loans. [7](#0-6) 

## Recommendation

Two options:

1. **Check `sender` instead of `owner`**: Change `beforeAddLiquidity` to gate on the first (currently unnamed) `sender` parameter — the address that called `pool.addLiquidity`. Pool admins would allowlist the LiquidityAdder for router-mediated deposits and individual EOAs for direct deposits.

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
  3. pool.addLiquidity(alice, salt, deltas, abi.encode(KIND_PAY), extensionData)
       msg.sender = LiquidityAdder
  4. _beforeAddLiquidity(LiquidityAdder, alice, ...)
  5. DepositAllowlistExtension.beforeAddLiquidity(LiquidityAdder, alice, ...)
       allowedDepositor[pool][alice] == true  → NO REVERT
  6. LiquidityLib.addLiquidity mints shares to alice
  7. Callback pulls tokens from Bob (payer stored in transient context)

Result:
  Bob (unauthorized) has added liquidity to the restricted pool.
  Alice holds the LP shares; Bob paid the tokens.
  The deposit allowlist is bypassed.
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L10-13)
```text
/// @title DepositAllowlistExtension
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
