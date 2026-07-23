The code confirms the claim. Let me verify the key facts:

1. `MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and caller-supplied `owner` as the position recipient to `_beforeAddLiquidity`. [1](#0-0) 

2. `DepositAllowlistExtension.beforeAddLiquidity` silently drops the first argument (sender) and gates on `owner` instead. [2](#0-1) 

3. `_validateOwner` in `MetricOmmPoolLiquidityAdder` only rejects `address(0)`, not mismatched owner/payer. [3](#0-2) 

The bypass is real and requires no special privileges — only a contract implementing `metricOmmModifyLiquidityCallback` and knowledge of one allowlisted address (readable from the public `allowedDepositor` mapping). [4](#0-3) 

---

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates position recipient (`owner`) instead of actual depositor (`sender`), allowing full allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the `msg.sender` of `addLiquidity`, who pays tokens via callback) and instead checks `owner` (the position recipient, freely supplied by the caller). Because any caller can set `owner` to any allowlisted address, an unpermissioned address can bypass the deposit allowlist entirely. This breaks the sole on-chain access-control mechanism pool admins have to restrict liquidity provision.

## Finding Description
`MetricOmmPool.addLiquidity` dispatches the before-hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
//                  ^^^^^^^^^^  ^^^^^
//                  actual payer  caller-supplied position recipient
```

The hook implementation ignores the first argument and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

**Exploit path:** Bob (unpermissioned, a contract implementing `metricOmmModifyLiquidityCallback`) calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")` where Alice is allowlisted. The extension evaluates `allowedDepositor[pool][alice]` → `true` and does not revert. The pool then calls `metricOmmModifyLiquidityCallback` on Bob (`msg.sender` of `addLiquidity`), so Bob's tokens are pulled and Alice receives the LP position. The allowlist gate is completely bypassed.

The same bypass applies through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`: `_validateOwner` only rejects `address(0)` and does not enforce `owner == msg.sender`, so Bob can pass Alice as `owner` through the adder as well.

No existing guard prevents this. The `allowedDepositor` mapping is public, so any attacker can read allowlisted addresses.

## Impact Explanation
`DepositAllowlistExtension` is the sole on-chain mechanism pool admins use to restrict who may provide liquidity. Checking `owner` instead of `sender` makes the gate trivially bypassable: any address can deposit into a restricted pool by naming an allowlisted address as `owner`. This constitutes an admin-boundary break — an unprivileged path bypasses an admin-configured access control — and allows unauthorized liquidity provision into pools designed for controlled LP sets. Additionally, it enables griefing of allowlisted LPs by forcing unwanted positions onto their addresses (positions they cannot refuse, since `removeLiquidity` requires `msg.sender == owner`).

## Likelihood Explanation
The bypass requires only: (1) knowing one allowlisted address, readable from the public `allowedDepositor` mapping; and (2) being a contract that implements `metricOmmModifyLiquidityCallback`. No special privileges, flash loans, or oracle manipulation are needed. Every pool deploying `DepositAllowlistExtension` with a non-empty allowlist is affected on every `addLiquidity` call.

## Recommendation
Replace the unnamed first argument with `sender` and gate on it instead of `owner`:

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

`sender` is `msg.sender` of the `addLiquidity` call — the entity that pays tokens via the callback — which is the economically relevant identity the allowlist is meant to gate.

## Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension` configured as the `beforeAddLiquidity` hook.
2. Call `extension.setAllowedToDeposit(pool, alice, true)` from the pool admin. Bob is **not** allowlisted.
3. Deploy a contract as Bob that implements `metricOmmModifyLiquidityCallback` to pay tokens.
4. From Bob's contract, call `pool.addLiquidity(alice, 0, deltas, callbackData, "")`.
5. Observe: the extension evaluates `allowedDepositor[pool][alice]` → `true` and does **not** revert.
6. The pool calls `metricOmmModifyLiquidityCallback` on Bob; Bob's tokens are pulled and Alice receives the LP shares.
7. Bob has successfully deposited into a pool he was not permitted to access, bypassing the allowlist entirely.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-14)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L247-249)
```text
  function _validateOwner(address owner) internal pure {
    if (owner == address(0)) revert InvalidPositionOwner();
  }
```
