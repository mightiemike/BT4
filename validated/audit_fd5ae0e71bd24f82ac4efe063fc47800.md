Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` checks `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and enforces the allowlist only against `owner` (the LP-position recipient). Because `MetricOmmPool.addLiquidity` imposes no `msg.sender == owner` constraint — unlike `removeLiquidity` which does — any unprivileged address can bypass the guard by supplying an allowlisted address as `owner` while itself providing the tokens via callback.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`, with no requirement that they be equal: [1](#0-0) 

By contrast, `removeLiquidity` enforces `msg.sender == owner` before proceeding: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` leaves the first positional argument (`sender`) unnamed and evaluates the allowlist only against `owner`: [3](#0-2) 

The sibling `SwapAllowlistExtension.beforeSwap` correctly checks `sender`: [4](#0-3) 

The exploit path is: attacker `eve` calls `pool.addLiquidity(alice, ...)` where `alice` is allowlisted. The extension receives `(sender=eve, owner=alice)`, evaluates `allowedDepositor[pool][alice] == true`, and does not revert. `eve`'s callback transfers the tokens; the LP position is minted to `alice`. The actual token provider (`eve`) is never checked.

## Impact Explanation
The deposit allowlist guard is fully bypassed. Any unauthorized address can deposit tokens into pools gated by KYC/AML, institutional, or whitelist controls. Tokens from an unrestricted source enter the pool's bin accounting (`binTotals`), breaking the access-control invariant the pool admin intended to enforce over who provides liquidity. This constitutes a broken core pool access-control mechanism with direct fund-flow impact (unauthorized capital enters restricted pools).

## Likelihood Explanation
No special privilege, flash loan, or oracle manipulation is required. The only prerequisite is knowing one allowlisted address, which is publicly readable via `allowedDepositor(pool, addr)`. Any EOA or contract can call `pool.addLiquidity(allowlisted_address, ...)` directly and repeatably.

## Recommendation
Check `sender` (the actual token provider) instead of `owner`, mirroring `SwapAllowlistExtension`:

```diff
- function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
+ function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
      external view override returns (bytes4)
  {
-     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
+     if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
          revert IMetricOmmPoolActions.NotAllowedToDeposit();
      }
      return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```

If the intended semantic is to gate who may *hold* LP positions (i.e., restrict `owner`), the allowlist mapping and documentation must be updated accordingly, and a separate `sender` check should be added if token-provider restriction is also desired.

## Proof of Concept
1. Pool is deployed with `DepositAllowlistExtension` in the `BEFORE_ADD_LIQUIDITY_ORDER` slot.
2. Pool admin calls `setAllowedToDeposit(pool, alice, true)` — only `alice` is allowlisted.
3. Unauthorized address `eve` calls:
   ```solidity
   pool.addLiquidity(
       alice,        // owner ← allowlisted, passes the check
       salt,
       deltas,
       callbackData, // eve implements the callback and transfers tokens
       extensionData
   );
   ```
4. `_beforeAddLiquidity(msg.sender=eve, owner=alice, ...)` is called at line 191.
5. `DepositAllowlistExtension.beforeAddLiquidity` evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
6. `LiquidityLib.addLiquidity` mints the LP position to `alice`; `eve`'s callback transfers the tokens into the pool.
7. `eve` has deposited into the restricted pool; the allowlist guard was never applied to the actual depositor. [3](#0-2) [5](#0-4)

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
