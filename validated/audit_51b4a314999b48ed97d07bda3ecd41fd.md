Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and checks only `owner` — the position recipient, which is a free caller-supplied parameter. Because any caller can set `owner` to any allowlisted address, the deposit allowlist is entirely bypassable by an unprivileged actor. The sibling `SwapAllowlistExtension` correctly checks `sender`, confirming the asymmetry is a defect.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully to the extension: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards the first argument (`sender`) and checks only `owner`: [3](#0-2) 

`owner` is a free parameter — any caller can set it to any address. When Bob (not allowlisted) calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, alice, ...)`, the adder calls `pool.addLiquidity(alice, ...)`. The pool passes `sender=LiquidityAdder` and `owner=alice` to the extension. The extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert. Bob's tokens are pulled via the callback; Alice receives the LP position. The actual initiator and payer (Bob) is never checked. [4](#0-3) 

`SwapAllowlistExtension.beforeSwap` correctly checks `sender`, not `recipient`: [5](#0-4) 

No other guard in the call path restricts the caller. `_validateOwner` only rejects `address(0)`: [6](#0-5) 

## Impact Explanation

The deposit allowlist is the sole on-chain mechanism a pool admin has to restrict who may add liquidity. With this bug the restriction is entirely ineffective: any address can add liquidity to an allowlist-gated pool by nominating any allowlisted address as `owner`. The admin-configured access boundary is broken, and any regulatory, economic, or security rationale behind the allowlist is voided. This is a direct admin-boundary break reachable by an unprivileged path with no special preconditions.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidityExactShares` call through `MetricOmmPoolLiquidityAdder` with `owner` set to any address that appears in `allowedDepositor`. The allowlist of a live pool is readable on-chain. No privileged role, flash loan, or price manipulation is needed. Any actor who wants to add liquidity to a restricted pool can do so immediately and repeatedly.

## Recommendation

Replace the `owner` check with a `sender` check, consistent with `SwapAllowlistExtension`:

```solidity
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

If the intent is to allowlist position owners rather than callers, the mapping name (`allowedDepositor`), NatSpec, and the inconsistency with `SwapAllowlistExtension` must be explicitly documented.

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`. `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       alice,   // owner — allowlisted
       salt,
       deltas,
       maxAmount0,
       maxAmount1,
       extensionData
   );
   ```
4. `MetricOmmPoolLiquidityAdder` calls `pool.addLiquidity(alice, ...)`.
5. Pool calls `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
7. Bob's tokens are pulled via the callback; Alice receives the LP position.
8. The allowlist is bypassed: Bob, a non-allowlisted address, has successfully added liquidity to the restricted pool.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L88-98)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```
