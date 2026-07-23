Audit Report

## Title
`DepositAllowlistExtension` checks `owner` instead of `sender`, allowing any caller to bypass the deposit allowlist — (File: metric-periphery/contracts/extensions/DepositAllowlistExtension.sol)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` discards the `sender` parameter (the actual caller/operator) and gates deposits solely on `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender != owner`, any unprivileged address can bypass the allowlist by supplying an allowlisted address as `owner` while acting as the real depositor. The sibling `SwapAllowlistExtension` correctly checks `sender`, confirming the deposit extension checks the wrong address.

## Finding Description
`MetricOmmPool.addLiquidity` passes the actual caller as the first argument to the extension hook:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

The NatDoc for `addLiquidity` explicitly documents the operator pattern:
> `msg.sender` pays but need not equal `owner` (operator pattern). [1](#0-0) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but silently discards it (unnamed `address`), then checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension caller), so the check resolves to `allowedDepositor[pool][owner]`. The actual depositing address (`sender`, i.e., the original `msg.sender` to the pool) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [3](#0-2) 

The operator pattern is actively used by `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`, which calls `pool.addLiquidity(owner, ...)` with `msg.sender` (the adder contract) as the actual caller and an arbitrary `owner` as the position recipient: [4](#0-3) 

The pool's `removeLiquidity` enforces `msg.sender == owner`, so LP shares credited to `owner` can only be withdrawn by `owner` — meaning the attacker must control the allowlisted `owner` address to extract funds, but the deposit allowlist bypass itself is unconditional. [5](#0-4) 

## Impact Explanation
A pool admin who deploys a permissioned pool with `DepositAllowlistExtension` intends to restrict which addresses may deposit. That restriction is fully circumvented: any address not on the allowlist can call `pool.addLiquidity(allowlistedAddress, salt, deltas, callbackData, extensionData)` directly. The extension sees `owner = allowlistedAddress` → check passes. The pool issues a `metricOmmModifyLiquidityCallback` to the actual caller, who pays the tokens. LP shares are credited to `allowlistedAddress`. If the attacker controls both an allowlisted address and an unlisted address, they can deposit from the unlisted address and withdraw from the allowlisted address, fully bypassing the pool admin's access control boundary. This constitutes an admin-boundary break: an unprivileged path bypasses a pool admin's configured security restriction.

## Likelihood Explanation
- The operator pattern (`msg.sender != owner`) is explicitly documented and supported by `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)`.
- No additional privilege is required; any EOA or contract can call `pool.addLiquidity` directly.
- The bypass requires only that the attacker know one allowlisted address, which is publicly readable from the `allowedDepositor` mapping.
- Likelihood is **High** given the trivial trigger and zero privilege requirement.

## Recommendation
Change `DepositAllowlistExtension.beforeAddLiquidity` to check `sender` (the actual caller/operator) instead of `owner`, mirroring the pattern used in `SwapAllowlistExtension`:

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

If the intent is to gate by position owner (not caller), the NatDoc and admin-facing setter must be updated to make this explicit, and the `SwapAllowlistExtension` pattern should be documented as intentionally different.

## Proof of Concept
```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner    = alice,   // allowlisted address bob does not own
      salt     = 1,
      deltas   = <valid bins>,
      callbackData = <bob pays tokens in callback>,
      extensionData = ""
  )

Extension check:
  allowedDepositor[pool][alice] == true  →  passes (bob's address never checked)

Result:
  bob pays tokens, alice receives LP shares
  bob controls alice → bob withdraws via alice.removeLiquidity(alice, 1, ...)
  Deposit allowlist fully bypassed with zero privilege
```

### Citations

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L146-147)
```text
  /// @notice Mint shares across bins for `(owner, salt)`; pulls tokens via `IMetricOmmModifyLiquidityCallback` on `msg.sender`.
  /// @dev Callback receives native token amounts the pool expects; underpay reverts `InsufficientTokenBalance`. If `DEPOSIT_ALLOWLIST_PROVIDER` is set, `owner` must pass allowlist. `msg.sender` pays but need not equal `owner` (operator pattern).
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

**File:** metric-core/contracts/MetricOmmPool.sol (L204-206)
```text
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
```
