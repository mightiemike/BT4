Audit Report

## Title
Missing `owner` Zero-Address Validation in `addLiquidity` Permanently Locks LP Principal — (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` address and passes it directly into `LiquidityLib.addLiquidity` and the extension hooks with no `require(owner != address(0))` guard. If `owner == address(0)`, the LP position is keyed under the zero address and the deposited tokens are irrecoverably locked because `removeLiquidity` enforces `msg.sender == owner`, a condition that can never be satisfied for `address(0)`.

## Finding Description
`addLiquidity` (lines 182–196, `MetricOmmPool.sol`) performs two input checks — empty `binIdxs` and length mismatch — but no zero-address check on `owner`:

```solidity
function addLiquidity(
  address owner, uint80 salt, LiquidityDelta calldata deltas,
  bytes calldata callbackData, bytes calldata extensionData
) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) {
  if (deltas.binIdxs.length == 0) return (0, 0);
  if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
  _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
  (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
    _liquidityContext(), owner, salt, deltas, callbackData, ...
  );
```

Inside `LiquidityLib.addLiquidity` (line 72), the position key is computed as `keccak256(abi.encode(owner, salt, int8(binIdx)))`. With `owner == address(0)`, shares are credited to `_positionBinShares[keccak256(abi.encode(address(0), salt, bin))]` and `binTotals` is updated normally — the deposit is fully accounted for.

`removeLiquidity` (line 206, `MetricOmmPool.sol`) enforces:
```solidity
if (msg.sender != owner) revert NotPositionOwner();
```
Because `msg.sender` is always a non-zero address in EVM execution, this check can never pass when `owner == address(0)`. No other code path — no admin function, no factory override, no fallback in `removeLiquidity` — can bypass this check or rescue the position. The `collectFees` function only collects spread/notional fees, not LP principal. The extension hook system (`_beforeAddLiquidity`) could theoretically reject `address(0)`, but only if the pool was deployed with an extension that performs this check; the core contract provides no such guarantee.

## Impact Explanation
Any tokens deposited via `addLiquidity(..., address(0), ...)` are permanently locked inside the pool. `binTotals.scaledToken0` / `scaledToken1` are incremented, inflating LP share accounting, but no address can ever satisfy `msg.sender == address(0)` to reclaim the principal. This is a direct, permanent loss of user principal with zero recovery path — matching the "Critical/High/Medium direct loss of user principal" allowed impact gate.

## Likelihood Explanation
The function is permissionless and callable by any EOA or contract. The scenario is reachable via a copy-paste error, a front-end bug passing a default-initialized address, or a programmatic integration that fails to populate the `owner` field. No privileged setup is required. Likelihood is low-to-medium for individual users, but the impact is total and irreversible for any affected deposit.

## Recommendation
Add an explicit zero-address check at the top of `addLiquidity`, before any state changes or extension calls:

```solidity
require(owner != address(0), InvalidOwner());
```

Optionally, add the same guard inside `LiquidityLib.addLiquidity` as defense-in-depth.

## Proof of Concept
1. Alice calls `pool.addLiquidity(address(0), salt, deltas, callbackData, extensionData)` and satisfies the token transfer via the modify-liquidity callback.
2. `LiquidityLib.addLiquidity` records shares under `_positionBinShares[keccak256(abi.encode(address(0), salt, binIdx))]` and increments `binTotals`.
3. Alice attempts `pool.removeLiquidity(address(0), salt, deltas, extensionData)` — execution reverts immediately at `if (msg.sender != owner) revert NotPositionOwner()` because Alice's address `!= address(0)`.
4. No other caller, including the factory or any admin, can satisfy `msg.sender == address(0)`.
5. The deposited token0 and token1 amounts remain in the pool forever, permanently locked.