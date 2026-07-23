Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing allowlist bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension` is documented as gating `addLiquidity` by depositor address, but its `beforeAddLiquidity` hook silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and checks `owner` (the LP-position beneficiary) instead. This makes the allowlist trivially bypassable by any unprivileged address and simultaneously breaks legitimate router-based deposit flows where an allowlisted router deposits on behalf of a non-allowlisted owner.

## Finding Description
`MetricOmmPool.addLiquidity` invokes the hook as:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional argument (`sender`) is unnamed and unused. The guard evaluates only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    ...
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [4](#0-3) 

The contract NatDoc and the `depositor` parameter name in `setAllowedToDeposit` confirm the intent is to gate the actual depositing address, not the LP-position owner. [5](#0-4) [6](#0-5) 

The wrong value checked is `allowedDepositor[msg.sender][owner]` — it should be `allowedDepositor[msg.sender][sender]`.

## Impact Explanation
**Bypass path (unauthorized deposit):** A non-allowlisted address `B` calls `pool.addLiquidity(owner=allowlistedAddress_A, ...)`. The hook receives `(sender=B, owner=A)`, checks only `A` (allowlisted), and passes. `B` provides tokens via the callback mechanism and the LP position is minted to `A`. The pool admin's KYC/access-control boundary is silently circumvented by any unprivileged caller — constituting an admin-boundary break with direct fund impact (unauthorized tokens enter the pool).

**Blocking path (broken router integration):** A pool admin allowlists router `R`. When a user `U` calls the router, the router calls `pool.addLiquidity(owner=U, ...)`. The hook checks `owner=U` (not allowlisted) and reverts. The allowlisted router is permanently unable to deposit on behalf of any user, making the pool's `addLiquidity` flow unusable through any intermediary — broken core pool functionality.

## Likelihood Explanation
Any pool deploying `DepositAllowlistExtension` with `allowAllDepositors == false` is immediately affected. The bypass requires no special privilege — any EOA or contract can trigger it by passing an allowlisted address as `owner`. The blocking path is triggered by the standard router-based deposit pattern. Both paths are reachable without any privileged setup by the attacker.

## Recommendation
Replace the unnamed first parameter with `sender` and check it instead of `owner`, mirroring `SwapAllowlistExtension`:

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
  pool  = MetricOmmPool with DepositAllowlistExtension, allowAllDepositors[pool] = false
  admin calls setAllowedToDeposit(pool, alice, true)   // only alice is allowlisted
  bob   = non-allowlisted EOA

Attack (bypass):
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted — hook checks this and passes
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = <bob pays tokens in callback>,
      extensionData= ""
  )
  → _beforeAddLiquidity(msg.sender=bob, owner=alice, ...)
  → hook receives (sender=bob, owner=alice)
  → allowedDepositor[pool][alice] == true → no revert
  → bob's tokens enter the pool; alice receives LP shares
  → allowlist completely bypassed

Blocking (router):
  admin calls setAllowedToDeposit(pool, router, true)
  user calls router.deposit(pool, user, delta)
  router calls pool.addLiquidity(owner=user, ...)
  → hook receives (sender=router, owner=user)
  → allowedDepositor[pool][user] == false → revert NotAllowedToDeposit
  → router permanently blocked despite being allowlisted
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-38)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
```
