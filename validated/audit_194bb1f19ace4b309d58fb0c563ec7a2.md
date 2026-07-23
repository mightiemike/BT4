Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and instead validates `owner`, a free caller-supplied argument designating the LP share recipient. Because `owner` is attacker-controlled, any unprivileged address can bypass the deposit allowlist by naming any already-authorized address as `owner`, causing unauthorized capital to enter a restricted pool while LP shares are minted to the named authorized address.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` encodes and forwards both addresses to the extension: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` leaves the first parameter (`sender`) unnamed and only checks `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` (the actual caller): [4](#0-3) 

The exploit path is:
1. Attacker calls `pool.addLiquidity(owner = AUTHORIZED, ...)` where `AUTHORIZED` is any address in `allowedDepositor[pool]`.
2. `beforeAddLiquidity` receives `sender = ATTACKER`, `owner = AUTHORIZED`; it checks `allowedDepositor[pool][AUTHORIZED]` → passes.
3. `LiquidityLib.addLiquidity` pulls tokens from `ATTACKER` via the swap callback and mints LP shares to `AUTHORIZED`.
4. `removeLiquidity` enforces `msg.sender == owner`, so the attacker cannot reclaim shares — but unauthorized capital has entered the pool.

No existing guard prevents this: the allowlist mapping is keyed by depositor address, but the wrong address is looked up.

## Impact Explanation

The deposit allowlist is the pool admin's sole mechanism for restricting who may provide liquidity (regulatory compliance, curated LP sets, controlled bootstrapping). The bug allows any unprivileged address to deposit into a restricted pool, permanently violating the admin-boundary invariant. Unauthorized capital distorts bin balances and undermines any compliance or economic rationale behind the allowlist. This matches the "Admin-boundary break" impact criterion: an unprivileged path bypasses a pool admin access-control boundary.

## Likelihood Explanation

Exploitation requires only a standard `addLiquidity` call with a known authorized address as `owner`. The `allowedDepositor` mapping is public, so any observer can identify valid authorized addresses. No special privileges, flash loans, or oracle manipulation are needed. The attack is repeatable at will by any address. [5](#0-4) 

## Recommendation

Check `sender` (the actual depositor) instead of `owner` (the LP recipient), mirroring `SwapAllowlistExtension`:

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

If the intent is to gate both the depositor and the owner, both should be checked independently.

## Proof of Concept

```
Setup:
  pool P has DepositAllowlistExtension configured
  allowedDepositor[P][AUTHORIZED] = true
  allowedDepositor[P][ATTACKER]   = false (not set)

Attack:
  ATTACKER calls pool.addLiquidity(
      owner        = AUTHORIZED,
      salt         = 0,
      deltas       = <valid delta>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  msg.sender = P (the pool)
  owner      = AUTHORIZED
  allowedDepositor[P][AUTHORIZED] == true  → check passes

Result:
  - ATTACKER's tokens are pulled via metricOmmSwapCallback
  - LP shares are minted to AUTHORIZED
  - ATTACKER has deposited into a pool that explicitly excluded them
  - The allowlist invariant is permanently violated
``` [3](#0-2) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
