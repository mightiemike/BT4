Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller and token payer) and checks only `owner` (the position recipient) against the allowlist. Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any unprivileged address can specify an allowlisted address as `owner`, pay the tokens themselves via the liquidity callback, and pass the allowlist check — fully circumventing a pool guard the admin configured to restrict who may provide liquidity.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` receives two identity arguments but discards the first (`sender`):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner`, with no requirement that they match: [2](#0-1) 

`LiquidityLib.addLiquidity` then fires the token-payment callback on `msg.sender` (the actual caller), not on `owner`: [3](#0-2) 

LP shares are credited to `owner` via `positionBinShares[posKey]` keyed by `owner`: [4](#0-3) 

`removeLiquidity` enforces `msg.sender == owner`, so only `owner` can withdraw: [5](#0-4) 

**Exploit path:** An attacker (not on the allowlist) implements `IMetricOmmModifyLiquidityCallback` and calls `pool.addLiquidity(allowlistedUser, salt, deltas, callbackData, extensionData)`. The pool calls `_beforeAddLiquidity(attacker, allowlistedUser, ...)`. The extension checks `allowedDepositor[pool][allowlistedUser]` → `true` → no revert. `LiquidityLib` mints shares to `allowlistedUser` and fires the callback on the attacker, who pays the tokens. With `allowlistedUser`'s cooperation, they call `removeLiquidity` and split the proceeds. The allowlist is never enforced against the actual payer.

## Impact Explanation
This is a direct admin-boundary break. The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity (e.g., institutional-only pools, KYC-gated pools). The bypass allows any unprivileged address to inject liquidity into a restricted pool by routing through an allowlisted address as `owner`. With collusion, the attacker recovers their tokens and the allowlist provides zero protection. Even without collusion, the attacker can force unauthorized liquidity into the pool, altering its depth and price dynamics in ways the admin did not sanction. This meets the "admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path" impact criterion.

## Likelihood Explanation
High. The bypass requires no special role or privilege — only knowledge of one allowlisted address (which is public on-chain state readable from `allowedDepositor`) and the ability to call `pool.addLiquidity` directly with a custom callback contract. No existing guard in the pool or extension prevents this path.

## Recommendation
Check `sender` (the actual payer/caller) instead of `owner` in `beforeAddLiquidity`:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to gate both the payer and the position owner, both `sender` and `owner` should be checked.

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension`; only `allowlistedUser` is set in `allowedDepositor[pool]`.
2. Attacker (not allowlisted) deploys a contract implementing `IMetricOmmModifyLiquidityCallback` and calls:
   ```solidity
   pool.addLiquidity(allowlistedUser, salt, deltas, callbackData, extensionData);
   ```
3. Pool calls `_beforeAddLiquidity(attacker, allowlistedUser, ...)` → extension checks `allowedDepositor[pool][allowlistedUser]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` mints shares to `allowlistedUser`; callback fires on attacker, attacker pays tokens.
5. `allowlistedUser` calls `pool.removeLiquidity(allowlistedUser, ...)`, receives tokens, and shares proceeds with attacker.
6. Net result: attacker provided liquidity to a restricted pool; the deposit allowlist was never enforced against the actual payer.

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L72-121)
```text
          bytes32 posKey = _positionBinKey(owner, salt, int8(binIdx));
          uint256 binTotalSharesVal = binTotalShares[binIdx];
          uint256 userShares = positionBinShares[posKey];

          uint256 newUserShares = userShares + sharesToAdd;
          if (newUserShares < ctx.minimalMintableLiquidity) {
            revert IMetricOmmPoolActions.MinimalLiquidity(newUserShares, ctx.minimalMintableLiquidity);
          }

          BinState storage binState = binStates[binIdx];

          uint256 amount0Scaled = 0;
          uint256 amount1Scaled = 0;
          if (binTotalSharesVal == 0) {
            if (binIdx < curBinIdxCache) {
              amount1Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken1PerShareE18, sharesToAdd), 1e18);
            } else if (binIdx > curBinIdxCache) {
              amount0Scaled = Math.ceilDiv(_checkedMul(ctx.initialScaledToken0PerShareE18, sharesToAdd), 1e18);
            } else {
              uint256 token0Proportion = type(uint104).max - ctx.curPosInBin;
              uint256 token1Proportion = ctx.curPosInBin;
              amount0Scaled =
              (Math.mulDiv(
                  token0Proportion * ctx.initialScaledToken0PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
              amount1Scaled =
              (Math.mulDiv(
                  token1Proportion * ctx.initialScaledToken1PerShareE18,
                  sharesToAdd,
                  uint256(type(uint104).max) * 1e18,
                  Math.Rounding.Ceil
                ));
            }
          } else {
            amount0Scaled = Math.ceilDiv(_checkedMul(binState.token0BalanceScaled, sharesToAdd), binTotalSharesVal);
            amount1Scaled = Math.ceilDiv(_checkedMul(binState.token1BalanceScaled, sharesToAdd), binTotalSharesVal);
          }
          if (amount0Scaled > 0) {
            totalToken0ToAddScaled += amount0Scaled;
            binState.token0BalanceScaled = (uint256(binState.token0BalanceScaled) + amount0Scaled).toUint104();
          }
          if (amount1Scaled > 0) {
            totalToken1ToAddScaled += amount1Scaled;
            binState.token1BalanceScaled = (uint256(binState.token1BalanceScaled) + amount1Scaled).toUint104();
          }
          binTotalShares[binIdx] = binTotalSharesVal + sharesToAdd;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L144-155)
```text
      if (amount0Added > 0 || amount1Added > 0) {
        uint256 balance0Before = IERC20(ctx.token0).balanceOf(address(this));
        uint256 balance1Before = IERC20(ctx.token1).balanceOf(address(this));
        IMetricOmmModifyLiquidityCallback(msg.sender)
          .metricOmmModifyLiquidityCallback(amount0Added, amount1Added, callbackData);
        if (amount0Added > 0 && balance0Before + amount0Added > IERC20(ctx.token0).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
        if (amount1Added > 0 && balance1Before + amount1Added > IERC20(ctx.token1).balanceOf(address(this))) {
          revert IMetricOmmPoolActions.InsufficientTokenBalance();
        }
      }
```
