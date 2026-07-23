Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension` is documented to gate `addLiquidity` by depositor address, but `beforeAddLiquidity` silently discards the `sender` argument and checks `owner` (the LP-share recipient) instead. Any unprivileged address can bypass the allowlist by calling `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` with an allowlisted address as `owner`, injecting liquidity into a pool the admin intended to restrict.

## Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` drops the first parameter and gates on `owner`:

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

Its sibling `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller), confirming the intended design pattern.

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-share recipient to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` forwards both arguments faithfully to the extension: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts a caller-controlled `owner` and calls `pool.addLiquidity(positionOwner, ...)` with `msg.sender` as payer — meaning the pool's `msg.sender` (the adder) is passed as `sender` to the extension, while the attacker-chosen `owner` is what the extension actually checks: [3](#0-2) 

**Full call trace for the bypass:**
1. Bob (not allowlisted) calls `adder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")`.
2. Adder calls `pool.addLiquidity(alice, salt, deltas, ...)` — `msg.sender` to pool is the adder.
3. Pool calls `_beforeAddLiquidity(adder, alice, ...)`.
4. Pool calls `extension.beforeAddLiquidity(adder, alice, ...)` — `msg.sender` to extension is the pool.
5. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. Bob's tokens are pulled via callback; Alice receives LP shares. Allowlist fully bypassed.

The `_validateOwner` check in the adder only rejects `address(0)`, providing no allowlist enforcement: [4](#0-3) 

## Impact Explanation

The deposit allowlist — the only on-chain guard restricting who may provide liquidity to a restricted pool — is rendered ineffective. Any unprivileged address can inject liquidity into a pool the admin intended to gate (e.g., KYC-gated, whitelist-only LP programs), violating the admin-boundary invariant. Unauthorized parties can effectively participate in fee accrual and LP withdrawal flows through allowlisted recipient addresses, breaking the core deposit-control invariant the extension exists to enforce.

## Likelihood Explanation

The bypass requires only: (a) knowledge of one allowlisted address (readable from on-chain `AllowedToDepositSet` events emitted by `setAllowedToDeposit`), and (b) a call to the publicly deployed `MetricOmmPoolLiquidityAdder`. No privileged access, flash loans, or special tokens are needed. Every pool deploying `DepositAllowlistExtension` without `allowAllDepositors = true` is affected. [5](#0-4) 

## Recommendation

Change `beforeAddLiquidity` to gate on `sender` (the actual caller/payer), consistent with `SwapAllowlistExtension.beforeSwap`:

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

If the intent is to gate on the LP-share recipient (`owner`), the NatSpec, event names, setter names, and the `addLiquidityExactShares(pool, owner, ...)` overload in `MetricOmmPoolLiquidityAdder` must all be updated to reflect that semantic — and the overload should be reconsidered since it lets any caller assign LP shares to an allowlisted owner.

## Proof of Concept

```solidity
// Pool deployed with DepositAllowlistExtension; alice allowlisted, bob is not.
extension.setAllowedToDeposit(address(pool), alice, true);

// Bob bypasses the allowlist via LiquidityAdder
vm.prank(bob);
adder.addLiquidityExactShares(
    address(pool),
    alice,   // owner = allowlisted address; bob pays, alice receives LP shares
    salt,
    deltas,
    max0,
    max1,
    ""
);
// No revert — allowlist bypassed. Bob's tokens pulled, alice receives LP shares.

// Confirm direct call from bob (owner=bob) correctly reverts
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToDeposit.selector);
pool.addLiquidity(bob, salt, deltas, callbackData, "");
```

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }
```
