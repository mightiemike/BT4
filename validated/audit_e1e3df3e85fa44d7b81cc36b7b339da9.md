Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Fully Bypassing the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual token-supplying caller) and gates on `owner` (the position beneficiary chosen by the caller). Any unprivileged address can bypass the allowlist by calling `pool.addLiquidity(allowlisted_address, ...)`, naming an already-approved address as `owner`. The pool admin's primary access-control tool for restricted pools is rendered ineffective.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both `sender` and `owner` to the extension via `abi.encodeCall`. However, `DepositAllowlistExtension.beforeAddLiquidity` declares the first parameter (`sender`) as unnamed, discarding it entirely, and checks `owner` instead: [2](#0-1) 

The admin-facing setter names its parameter `depositor`, confirming the intended subject of the check is the caller, not the beneficiary: [3](#0-2) 

The sister extension `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (first parameter) and ignores `recipient` (second parameter), establishing the intended pattern: [4](#0-3) 

**Exploit path:**
1. Pool admin configures `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`.
2. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The hook checks `allowedDepositor[pool][alice]` → passes.
4. The pool calls `IMetricOmmSwapCallback(Bob).metricOmmSwapCallback(...)` to collect tokens from Bob.
5. Bob's tokens enter the pool; Alice receives the position shares.
6. Since `removeLiquidity` enforces `msg.sender == owner`, only Alice can withdraw — Bob's tokens are permanently locked unless Alice cooperates: [5](#0-4) 

## Impact Explanation

The deposit allowlist — the pool admin's primary mechanism for restricting who may supply liquidity (KYC, compliance, market-maker-only pools) — is fully bypassed by any unprivileged caller. Tokens from non-allowlisted sources enter pools explicitly configured to exclude them. Additionally, the forced position on Alice and the locked tokens constitute a griefing vector against both the caller (funds locked) and the allowlisted address (unwanted position forced upon them). This is a broken core pool invariant (admin-boundary break: allowlist bypassed by an unprivileged path) with direct fund impact.

## Likelihood Explanation

The bypass requires no special privilege. Any EOA or contract can call `addLiquidity` with an allowlisted address as `owner`. The `allowedDepositor` mapping is public, so an attacker can trivially identify a valid `owner`. The only cost is the tokens the attacker must supply via the callback. The attack is repeatable and permissionless.

## Recommendation

Replace the unnamed first parameter with `sender` and gate on it, mirroring `SwapAllowlistExtension`:

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

This ensures the check targets the address that actually provides tokens, consistent with the `setAllowedToDeposit`/`depositor` naming and the `SwapAllowlistExtension` pattern.

## Proof of Concept

```solidity
// Pool has DepositAllowlistExtension configured.
// Pool admin called: extension.setAllowedToDeposit(pool, alice, true);
// Bob is NOT in the allowlist.

// Bob calls addLiquidity naming alice as owner:
pool.addLiquidity(
    alice,        // owner — passes the allowlist check (bug: sender is not checked)
    salt,
    deltas,
    callbackData, // Bob's contract supplies tokens in the callback
    extensionData
);
// Result: hook checks allowedDepositor[pool][alice] → true → passes.
// Bob's tokens enter the pool. Alice receives the position.
// Bob cannot recover tokens (removeLiquidity requires msg.sender == owner == alice).
// The allowlist guard never checked Bob.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
