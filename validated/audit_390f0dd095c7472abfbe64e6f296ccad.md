Audit Report

## Title
`DepositAllowlistExtension` checks LP position `owner` instead of transaction `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token provider) and checks the `owner` parameter (the LP position recipient) against the allowlist. Any unprivileged address can bypass the guard by calling `addLiquidity(allowlisted_address, ...)`, causing their tokens to enter a permissioned pool while the LP position is credited to the allowlisted address. This breaks the admin-configured access-control invariant for permissioned liquidity pools.

## Finding Description
`MetricOmmPool.addLiquidity` invokes the hook as `_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData)` at [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes both as `(sender, owner, ...)` and forwards them to the extension: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and silently dropped. The allowlist check is performed exclusively on `owner`: [3](#0-2) 

The contract's NatSpec states "Gates `addLiquidity` by depositor address, per pool", and all admin-facing functions use the term `depositor`, confirming the intent is to gate the actual depositor (`sender`), not the LP position recipient (`owner`): [4](#0-3) 

After the hook passes, `LiquidityLib.addLiquidity` issues the token-pull callback on `msg.sender` (the actual caller, Bob), not on `owner` (Alice): [5](#0-4) 

The library comment confirms that under DELEGATECALL, `msg.sender` is the original external caller: [6](#0-5) 

**Attack path:**
1. Pool admin deploys a pool with `DepositAllowlistExtension` in the `beforeAddLiquidity` hook order and allowlists only `alice` via `setAllowedToDeposit(pool, alice, true)`.
2. Bob (not allowlisted) calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. The extension checks `allowedDepositor[pool][alice]` → `true` → passes.
4. `LiquidityLib.addLiquidity` issues `IMetricOmmModifyLiquidityCallback(msg.sender).metricOmmModifyLiquidityCallback(...)` on Bob to pull tokens.
5. Bob's tokens enter the pool; Alice receives the LP position (shares credited to Alice's position key via `_positionBinKey(owner, salt, binIdx)`).
6. Bob has deposited into a permissioned pool without being allowlisted.

## Impact Explanation
The deposit allowlist guard is completely bypassed by any unprivileged caller. For pools configured as permissioned liquidity pools (institutional, regulatory, or strategy-constrained), unauthorized parties can inject liquidity at will, violating the pool admin's access-control invariant. This is an **admin-boundary break**: a factory/pool-admin-configured guard is bypassed by an unprivileged path, satisfying the allowed impact gate.

## Likelihood Explanation
**High.** No special privileges, flash loans, or complex setup are required. Any caller can trivially bypass the allowlist by specifying any allowlisted address as `owner`. The bypass is unconditionally available as long as at least one address is allowlisted. The attacker only needs to know one allowlisted address, which may be observable on-chain from prior `AllowedToDepositSet` events.

## Recommendation
Name the first parameter `sender` and check it instead of `owner`:

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
```
Setup:
  pool configured with DepositAllowlistExtension on beforeAddLiquidity
  setAllowedToDeposit(pool, alice, true)  // only alice is allowlisted
  bob is NOT allowlisted

Attack:
  bob calls pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)

Trace:
  MetricOmmPool.addLiquidity:
    _beforeAddLiquidity(msg.sender=bob, owner=alice, ...)
      → DepositAllowlistExtension.beforeAddLiquidity(address /*bob, dropped*/, alice, ...)
        → allowedDepositor[pool][alice] == true → PASSES
  LiquidityLib.addLiquidity(owner=alice, ...):
    → IMetricOmmModifyLiquidityCallback(msg.sender=bob).metricOmmModifyLiquidityCallback(...)
    → bob's tokens transferred into pool
    → LP shares credited to alice's position key

Result:
  bob deposited into permissioned pool without being allowlisted
  alice receives LP position funded by bob's tokens
```

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-29)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
  mapping(address pool => bool) public allowAllDepositors;

  constructor(address factory_) BaseMetricExtension(factory_) {}

  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
  }

  function setAllowAllDepositors(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllDepositors[pool_] = allowed;
    emit AllowAllDepositorsSet(pool_, allowed);
  }

  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L17-17)
```text
///      - `msg.sender` is the original external caller.
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L147-148)
```text
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
```
