Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead checks whether the `owner` (the position recipient) is allowlisted. Because `MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any caller with no `msg.sender == owner` guard, any unprivileged address can bypass the deposit allowlist by supplying an already-allowlisted address as `owner`. The unauthorized depositor's tokens are credited to the named `owner`'s position, which the `owner` can then withdraw via `removeLiquidity`.

## Finding Description
`MetricOmmPool.addLiquidity` accepts an arbitrary `owner` from any external caller and passes `msg.sender` as `sender` to the extension hook: [1](#0-0) 

There is no `msg.sender == owner` guard anywhere in `addLiquidity` (contrast with `removeLiquidity` at line 206, which does enforce `msg.sender == owner`). [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `(sender, owner, ...)` but discards `sender` (unnamed first parameter) and checks only `owner`: [3](#0-2) 

The allowlist mapping is `allowedDepositor[pool][depositor]`, intended to gate the actual depositor: [4](#0-3) 

Because `owner` is fully attacker-controlled and `allowedDepositor[pool][allowlistedAddress]` evaluates to `true`, the guard is trivially bypassed. The correct pattern is already used by `SwapAllowlistExtension.beforeSwap`, which checks `sender` (the actual swapper): [5](#0-4) 

## Impact Explanation
The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity to a curated pool. With this bypass, any unprivileged address can add liquidity to a curated pool, breaking the admin-boundary invariant. The unauthorized depositor's funds are credited to the named `owner`'s position; a colluding pair (attacker + allowlisted accomplice) can have the accomplice call `removeLiquidity` to recover the funds, effectively laundering unauthorized liquidity through the pool. Pool state (bin totals, share accounting) is mutated by an actor the admin explicitly excluded, causing LP share dilution for legitimate participants. This matches the allowed impact gate: **admin-boundary break** (factory/pool role checks bypassed by an unprivileged path) and **broken core pool functionality** with fund-impacting consequences.

## Likelihood Explanation
No special privilege is required — any EOA or contract can call `pool.addLiquidity` directly. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or from emitted `AllowedToDepositSet` events. No flash loan or multi-block setup is needed; a single transaction suffices. Likelihood: **High**.

## Recommendation
Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner` (the position recipient), mirroring the correct pattern in `SwapAllowlistExtension`:

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
1. Pool admin deploys a pool with `DepositAllowlistExtension` on the `beforeAddLiquidity` hook.
2. Admin calls `setAllowedToDeposit(pool, alice, true)` — only Alice is permitted to deposit.
3. Unauthorized Bob calls:
   ```solidity
   pool.addLiquidity(
       alice,        // owner = allowlisted address (attacker-controlled)
       salt,
       deltas,
       callbackData, // Bob's tokens are pulled here via callback
       extensionData
   );
   ```
4. Pool calls `_beforeAddLiquidity(sender=Bob, owner=Alice, ...)`.
5. Extension evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
6. Bob's tokens are deposited; Alice's position grows.
7. Alice calls `removeLiquidity` (enforced `msg.sender == owner` passes for Alice) and withdraws Bob's tokens.
8. The deposit allowlist is fully bypassed in a single unprivileged transaction.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
