Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual `msg.sender` of `addLiquidity`) and instead validates the caller-controlled `owner` parameter against the allowlist. Because `owner` is a free parameter in `MetricOmmPool.addLiquidity`, any address can bypass the deposit allowlist by nominating an already-allowlisted address as `owner`, while the actual token provider (the attacker) is never checked.

## Finding Description
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address distinct from `msg.sender`, and passes both to the extension hook: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` correctly encodes both `sender` (i.e., `msg.sender`) and `owner` in the call to the extension: [2](#0-1) 

However, `DepositAllowlistExtension.beforeAddLiquidity` discards `sender` (left unnamed) and checks only `owner`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender` and discards the second argument: [4](#0-3) 

The asymmetry is the root cause. Since `owner` is freely chosen by the caller, any address can pass the guard by specifying any allowlisted address as `owner`. The attacker provides tokens via the swap callback; LP shares are minted to the nominated `owner`. The actual depositor (`sender`) is never validated.

## Impact Explanation
The deposit allowlist is completely bypassed. An unauthorized address can deposit into any pool gated by `DepositAllowlistExtension`, modifying `binTotals`, `curPosInBin`, and per-position bin shares without authorization. This breaks the pool admin's access control invariant (e.g., KYC/compliance gating), allows unauthorized parties to alter pool state affecting oracle-derived prices and LP claims, and enables forced liquidity injection into pools not configured to accept it. This constitutes an admin-boundary break by an unprivileged path.

## Likelihood Explanation
`addLiquidity` is a public function with no role requirement. The only cost to the attacker is the tokens deposited, which are credited as LP shares to the nominated `owner`. Any address that can call the pool and hold the relevant tokens can exploit this. Pools deploying `DepositAllowlistExtension` for compliance or liquidity-concentration purposes are directly and fully affected.

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual depositor) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension`:

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
1. Pool is deployed with `DepositAllowlistExtension`; `allowedDepositor[pool][alice] = true`; `allowedDepositor[pool][attacker] = false`.
2. `attacker` calls `pool.addLiquidity(alice, salt, deltas, callbackData, extensionData)`.
3. Pool calls `_beforeAddLiquidity(msg.sender=attacker, owner=alice, ...)`, which encodes and forwards both to the extension.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert. `attacker` is never checked.
5. Pool proceeds; `attacker` provides tokens via callback; LP shares are minted to `alice`.
6. `attacker` has deposited into a pool that was supposed to block them, modifying bin balances and pool state without authorization. [3](#0-2) [5](#0-4)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
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
