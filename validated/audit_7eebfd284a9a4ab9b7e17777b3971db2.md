Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` who called `addLiquidity`) and instead gates access on the caller-supplied `owner` argument. Because `owner` is a free parameter any caller can set to any address, any non-allowlisted address can bypass the restriction by naming an already-allowlisted address as `owner`. The pool's core deposit access-control invariant is fully broken.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the extension hook: [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter (`sender`) entirely — it is unnamed — and checks only `owner`: [2](#0-1) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly gates on `sender` (the actual caller), not the free `recipient` parameter: [3](#0-2) 

The admin-facing setter names the gated entity `depositor`, confirming the intent is to restrict the calling address: [4](#0-3) 

No other guard in the `addLiquidity` path checks whether `msg.sender` is allowlisted. The `removeLiquidity` function enforces `msg.sender == owner`, meaning the allowlisted `owner` (Alice) retains full control of the minted LP shares and can withdraw them, making the bypass fully round-trippable. [5](#0-4) 

## Impact Explanation

Any address not on the allowlist can deposit into a restricted pool by supplying an allowlisted address as `owner`. The unauthorized caller's tokens enter the pool; the allowlisted address receives the LP shares. With collusion, the allowlisted address can call `removeLiquidity` and return the tokens off-chain, making the bypass fully round-trippable with no net loss to the attacker pair. Even without collusion, the pool receives liquidity from parties the admin explicitly excluded, violating the deposit access-control invariant and potentially diluting existing LPs or breaching regulatory/operational restrictions the allowlist was meant to enforce. This constitutes broken core pool functionality causing direct violation of an admin-enforced access boundary.

## Likelihood Explanation

The bypass requires only a single `addLiquidity` call with `owner` set to any allowlisted address. No special privileges, flash loans, oracle manipulation, or elevated roles are needed. Any address can execute it at any time the pool is not paused, and the attack is repeatable indefinitely.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// DepositAllowlistExtension.sol
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
-   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+   if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

## Proof of Concept

1. Pool is deployed with `DepositAllowlistExtension` configured; `allowAllDepositors[pool] = false`.
2. Admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
3. Bob calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice] == true` → no revert.
6. Bob's tokens are transferred into the pool; Alice receives the LP shares.
7. Alice calls `pool.removeLiquidity(owner=Alice, ...)` (passes the `msg.sender == owner` check) and withdraws the tokens, returning them to Bob off-chain.
8. Net result: Bob deposited into a pool he was explicitly barred from, with zero on-chain evidence of a policy violation.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
