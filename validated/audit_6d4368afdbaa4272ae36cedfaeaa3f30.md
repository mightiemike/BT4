Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity()` checks LP recipient `owner` instead of actual depositor `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity()` silently discards the `sender` argument (the actual caller paying tokens) and instead checks the `owner` argument (the LP position recipient), which is a free caller-controlled parameter in `pool.addLiquidity(address owner, ...)`. Any non-allowlisted address can pass the guard by naming any allowlisted address as `owner`, depositing tokens into a restricted pool while the allowlist check never fires against the real depositor.

## Finding Description
`MetricOmmPool.addLiquidity()` dispatches the hook as:

```solidity
// MetricOmmPool.sol:191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`msg.sender` (the actual depositor) is the first argument; `owner` (the LP recipient, a free parameter chosen by the caller) is the second. [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity()` leaves the first parameter unnamed and never reads it, then performs the allowlist lookup against `owner`:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

After the guard passes, `LiquidityLib.addLiquidity` calls back on `msg.sender` (the actual caller) to pull tokens, not on `owner`. The non-allowlisted caller pays the tokens; the allowlisted `owner` receives the LP shares; the guard never fires against the real depositor.

`SwapAllowlistExtension.beforeSwap()` correctly checks `sender` (the first parameter) for its analogous guard, confirming the asymmetry is a defect:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
``` [3](#0-2) 

## Impact Explanation
The deposit allowlist is rendered completely ineffective. Any non-allowlisted address can deposit tokens into a restricted pool by supplying any allowlisted address as `owner`. This constitutes an admin-boundary break via an unprivileged path: the pool admin's intended access control (regulatory compliance, KYC gating, liquidity composition control) is bypassed without any special privilege. The allowlisted `owner` receives an unsolicited LP position they did not initiate; the actual depositor's tokens are locked in the pool and recoverable only if `owner` cooperates to remove liquidity, constituting a direct fund-movement consequence.

## Likelihood Explanation
Exploitation requires a single direct call to `pool.addLiquidity(owner=<any_allowlisted_address>, ...)` with a valid callback implementation. No special permissions, flash loans, or multi-step setup are needed. The `MetricOmmPoolLiquidityAdder` router makes this reachable from EOAs via `addLiquidityExactShares(pool, owner, ...)` with `owner` set to any allowlisted address. The only prerequisite is knowing one allowlisted address, which is publicly readable from the `allowedDepositor` mapping. [4](#0-3) 

## Recommendation
Check `sender` (the actual depositor, first parameter) instead of `owner` (the LP recipient, second parameter), mirroring the correct pattern already used in `SwapAllowlistExtension.beforeSwap()`:

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
1. Deploy a pool with `DepositAllowlistExtension` wired into `BEFORE_ADD_LIQUIDITY_ORDER`.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted; `bob` is not.
3. `bob` (non-allowlisted, holding tokens and implementing `IMetricOmmModifyLiquidityCallback`) calls:
   ```solidity
   pool.addLiquidity(
       alice,       // owner — allowlisted, check passes
       salt,
       deltas,
       callbackData,
       extensionData
   );
   ```
4. `beforeAddLiquidity` checks `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib` calls `bob.metricOmmModifyLiquidityCallback(...)` — `bob` transfers tokens to the pool.
6. LP shares are credited to `alice` at key `keccak256(abi.encode(alice, salt, binIdx))`.
7. `bob` has deposited tokens into the restricted pool; the allowlist guard never triggered against `bob`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
