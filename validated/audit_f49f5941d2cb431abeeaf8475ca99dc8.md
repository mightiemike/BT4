Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Unprivileged Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument and checks only `owner` (the caller-supplied position recipient) against `allowedDepositor`. Because `owner` is a free parameter any caller can set, a non-allowlisted actor bypasses the guard by nominating any allowlisted address as `owner` while paying the tokens themselves. The deposit allowlist — the sole on-chain mechanism for restricting who may add liquidity — is rendered entirely ineffective.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument (unnamed, discarded) and `owner` as its second: [1](#0-0) 

The check `allowedDepositor[msg.sender][owner]` uses `msg.sender` as the pool key (correct) but `owner` as the depositor key (wrong). `owner` is a free parameter supplied by the caller.

The pool passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to `_beforeAddLiquidity`: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully to the extension: [3](#0-2) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (with explicit `owner`) stores `msg.sender` (Bob) as the `payer` in transient storage but passes the caller-supplied `owner` (Alice) directly to the pool: [4](#0-3) 

`_validateOwner` only rejects `address(0)` — it does not require `owner == msg.sender`: [5](#0-4) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller), confirming the deposit extension has the wrong field: [6](#0-5) 

The contract's own NatSpec ("Gates `addLiquidity` by depositor address") and the mapping name `allowedDepositor` confirm the intent is to check the actual depositor, not the position recipient.

## Impact Explanation

The deposit allowlist is the only on-chain mechanism a pool admin has to restrict who may add liquidity. With this bug the restriction is entirely ineffective: any address can add liquidity to an allowlist-gated pool by nominating any allowlisted address as `owner`. The pool receives liquidity from unauthorized sources, the admin-configured access boundary is broken, and any regulatory, economic, or security rationale behind the allowlist is voided. This is a direct admin-boundary break reachable by an unprivileged path with no special preconditions.

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

If the intent is to allowlist position owners rather than callers, the NatSpec, the mapping name (`allowedDepositor`), and the setter parameter name (`depositor`) must all be updated to reflect that, and the inconsistency with `SwapAllowlistExtension` must be explicitly documented.

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension`. `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, alice, true)`. Alice is the only allowlisted depositor.
3. Bob (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       alice,   // owner — allowlisted address
       salt,
       deltas,
       maxAmount0,
       maxAmount1,
       extensionData
   );
   ```
4. `MetricOmmPoolLiquidityAdder` stores Bob as `payer` in transient storage and calls `pool.addLiquidity(alice, ...)`.
5. Pool calls `_beforeAddLiquidity(msg.sender=LiquidityAdder, owner=alice, ...)`.
6. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
7. Bob's tokens are pulled via the callback (`payer = Bob`); Alice receives the LP position.
8. The allowlist has been bypassed: Bob, a non-allowlisted address, has successfully added liquidity to the restricted pool.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
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
