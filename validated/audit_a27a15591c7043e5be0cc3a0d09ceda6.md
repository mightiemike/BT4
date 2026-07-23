Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and only validates `owner`, which is a free caller-supplied argument. Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint, any unprivileged address can pass the allowlist gate by naming any observable allowlisted address as `owner`, while the liquidity callback pulls tokens from the actual caller.

## Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` and does not enforce `msg.sender == owner` (unlike `removeLiquidity`, which does enforce this at line 206): [1](#0-0) 

The pool passes both actors to the extension hook: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` encodes both `sender` and `owner` into the call: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` silently drops `sender` (unnamed first parameter) and only checks `owner`: [4](#0-3) 

After the extension check passes, `LiquidityLib.addLiquidity` mints shares to `owner` and fires the callback on `msg.sender` (the actual caller), pulling tokens from the caller: [5](#0-4) 

The result: Bob (not allowlisted) calls `pool.addLiquidity(alice, ...)`, the extension evaluates `allowedDepositor[pool][alice] == true` and does not revert, LP shares are minted to Alice, and Bob's tokens are pulled into the pool. The allowlist is fully bypassed.

## Impact Explanation
The deposit allowlist — a pool-admin-configured access control — is completely ineffective. Any unprivileged address can add liquidity to a restricted pool by supplying any observable allowlisted address as `owner`. This constitutes an admin-boundary break: the pool admin's intended access restriction is bypassed by an unprivileged path. Secondary consequences include unauthorized liquidity injections altering pool depth and fee dynamics, and griefing of allowlisted LPs by minting unwanted positions into their accounts without consent.

## Likelihood Explanation
High. Allowlisted addresses are observable on-chain via `AllowedToDepositSet` events or direct reads of `allowedDepositor`. No special privilege is required — any EOA or contract can call `pool.addLiquidity` directly (the pool imposes no caller restriction on `addLiquidity`). The attack is repeatable at any time.

## Recommendation
Check `sender` (the actual token-paying caller) instead of `owner`:

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

If the intent is to allow routers to act on behalf of allowlisted owners, both `sender` and `owner` should be checked (either is allowlisted).

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` configured as an extension with `beforeAddLiquidity` order set.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — Alice is allowlisted; Bob is not.
3. Bob (not allowlisted) directly calls:
   ```solidity
   pool.addLiquidity(alice, salt, deltas, callbackData, extensionData);
   ```
4. Pool calls `extension.beforeAddLiquidity(bob, alice, salt, deltas, "")`.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints LP shares to Alice's position key; the liquidity callback fires on Bob (`msg.sender` of `addLiquidity`), pulling Bob's tokens into the pool.
7. Bob has successfully deposited into a restricted pool. Alice holds LP shares she never requested. The deposit allowlist is bypassed.

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
