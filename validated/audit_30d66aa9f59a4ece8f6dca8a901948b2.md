Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing complete allowlist bypass — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the `owner` argument, which is freely chosen by the caller. Because `addLiquidity` imposes no restriction on who may be named as `owner`, any unauthorized address can bypass the deposit allowlist by specifying any allowlisted address as `owner`, depositing tokens into that address's LP position.

## Finding Description
`MetricOmmPool.addLiquidity` calls the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

The first argument is the actual caller (`msg.sender`); the second is the `owner` parameter supplied by that caller. Inside the hook, the first `address` parameter is unnamed and discarded:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [2](#0-1) 

The guard checks `allowedDepositor[msg.sender][owner]` where `msg.sender` is the pool and `owner` is the attacker-controlled address. Since `addLiquidity` places no restriction on the `owner` argument, any caller can pass an allowlisted address as `owner` to satisfy the check.

The `removeLiquidity` path enforces `msg.sender == owner`: [3](#0-2) 

This means the allowlisted address (`bob`) can later call `removeLiquidity` and withdraw the unauthorized depositor's tokens, completing the exploit.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual swap initiator), not the recipient: [4](#0-3) 

## Impact Explanation
The deposit allowlist — the sole purpose of deploying this extension — is completely bypassed. Any unauthorized user can inject funds into a curated pool by naming any allowlisted address as `owner`. The allowlisted address receives an unsolicited LP position (griefing vector) and can drain the deposited tokens via `removeLiquidity`. This constitutes an admin-boundary break via an unprivileged path and breaks core pool functionality for curated pools.

## Likelihood Explanation
High. Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events. The exploit requires no special privileges, no flash loan, and no complex setup — a single `addLiquidity` call suffices. Any unauthorized user can execute this in one transaction.

## Recommendation
Replace the `owner` check with the `sender` argument (the actual caller), mirroring the correct pattern in `SwapAllowlistExtension`:

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

## Proof of Concept
1. Deploy a pool with `DepositAllowlistExtension` configured in the `beforeAddLiquidity` order.
2. Pool admin allowlists `bob` but not `alice`: `extension.setAllowedToDeposit(pool, bob, true)`.
3. `alice` (unauthorized) calls `pool.addLiquidity(bob, salt, deltas, callbackData, extensionData)`.
4. The hook receives `sender = alice` (discarded), `owner = bob`; checks `allowedDepositor[pool][bob]` → `true` → passes.
5. Alice's tokens are transferred into the pool; the LP position is attributed to `bob`.
6. `bob` calls `pool.removeLiquidity(bob, salt, deltas, extensionData)` — passes the `msg.sender == owner` check — and withdraws Alice's tokens.
7. Alice has permanently lost her tokens; the allowlist was completely bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
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
