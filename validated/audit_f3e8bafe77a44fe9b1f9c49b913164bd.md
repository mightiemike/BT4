Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on `owner` Instead of `sender`, Allowing Non-Allowlisted Callers to Bypass the Deposit Gate — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` argument (the actual caller/payer) and checks only `owner` (the position recipient). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender` (payer) need not equal `owner` (position recipient), any non-allowlisted address can bypass the deposit gate by naming any allowlisted address as `owner`. The pool admin's deposit restriction is rendered entirely ineffective.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity` is declared with `sender` as the first parameter but leaves it unnamed and unused, checking only `owner`:

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
```

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as `owner` to the hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

Since `owner` is a free parameter supplied by the caller, any address can pass `owner = allowlisted_address` and the check `allowedDepositor[pool][allowlisted_address]` returns `true`, bypassing the gate. The actual payer (`sender`) is never validated.

By contrast, `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

The `DepositAllowlistExtension` is inconsistent with this pattern and with its own NatSpec ("Gates `addLiquidity` by depositor address").

## Impact Explanation
The deposit allowlist — a pool admin security control — is completely bypassed. Any non-allowlisted address can inject liquidity into a restricted pool by naming any allowlisted address as `owner`. The non-allowlisted address pays tokens via the modify-liquidity callback; the allowlisted `owner` receives the LP position. This breaks the core access-control invariant of the extension and constitutes broken core pool functionality. The allowlisted owner can subsequently call `removeLiquidity` (which enforces `msg.sender == owner`) to withdraw the deposited tokens, making this a viable griefing vector against the depositor or a fund-extraction path if the owner is a cooperating party.

## Likelihood Explanation
The operator pattern is a first-class, documented feature: `MetricOmmPoolLiquidityAdder` itself calls `pool.addLiquidity(owner, ...)` where `msg.sender` to the pool is the adder contract, not the user — demonstrating `sender != owner` is a routine, expected flow. Any user who knows a single allowlisted address (which may be publicly observable on-chain) can exploit this without any privileged access, repeatedly, against any pool using this extension.

## Recommendation
Mirror the pattern used in `SwapAllowlistExtension`: check `sender` (the actual depositor/payer), not `owner`:

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

If the intent is to gate by position owner rather than payer, the NatSpec, contract name, and storage mapping names must be updated to reflect that, and the bypass must be explicitly acknowledged.

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` attached to `beforeAddLiquidity`.
2. Admin calls `setAllowedToDeposit(pool, bob, true)`. `alice` is **not** allowlisted.
3. `alice` calls `pool.addLiquidity(bob /*owner*/, salt, deltas, callbackData, extensionData)`.
4. Pool calls `extension.beforeAddLiquidity(alice /*sender*/, bob /*owner*/, ...)`.
5. Extension evaluates `allowedDepositor[pool][bob]` → `true` → no revert.
6. Pool proceeds; `alice`'s callback pays tokens; `bob` receives the LP position.
7. `bob` calls `pool.removeLiquidity(bob, salt, deltas, "")` — passes `msg.sender == owner` check — and withdraws tokens.
8. `alice` has deposited into a restricted pool without being on the allowlist; the deposit gate is defeated. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L199-206)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
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
