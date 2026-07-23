Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` validates `owner` instead of `sender`, allowing any unprivileged caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards its first parameter (`sender`, the actual `msg.sender` of the pool call) and validates only `owner` (a caller-supplied argument). Because `MetricOmmPool.addLiquidity` explicitly supports an operator pattern where `msg.sender ≠ owner`, any address not on the allowlist can deposit into a restricted pool by supplying an allowlisted address as `owner`, fully bypassing the access-control guard without any privileges.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the second argument to `_beforeAddLiquidity`: [1](#0-0) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first positional parameter is unnamed and discarded; only `owner` is tested: [2](#0-1) 

Because `owner` is a free caller-supplied argument, any address can set it to any allowlisted address. The check `allowedDepositor[msg.sender][owner]` then passes even though the actual depositing address (`sender`) is not on the allowlist. The sibling `SwapAllowlistExtension.beforeSwap` correctly validates `sender` (the actual caller): [3](#0-2) 

The asymmetry between the two extensions confirms the deposit extension is checking the wrong field. Additionally, `removeLiquidity` enforces `msg.sender == owner`: [4](#0-3) 

This means the allowlisted `owner` (who never consented) must actively call `removeLiquidity` to recover the LP shares credited to them.

## Impact Explanation
A pool configured with `DepositAllowlistExtension` is intended to be a permissioned liquidity venue (e.g., KYC-gated, institutional-only). The bypass enables: (1) **Allowlist circumvention** — any unprivileged address can add liquidity to a pool that is supposed to be restricted, breaking the core access-control invariant the pool admin configured; (2) **Unsolicited LP position assignment** — the unauthorized caller pays tokens via the modify-liquidity callback, and the allowlisted `owner` receives LP shares they never requested and must actively unwind; (3) **Pool state manipulation** — an unauthorized actor can shift bin balances, alter the active-bin cursor, and affect swap pricing in a pool that was supposed to be closed to them. Severity is **Medium**: the allowlist guard is fully bypassed by an unprivileged path, breaking a core pool-access invariant and enabling unsolicited state changes, but the attacker does not directly steal funds from existing LPs.

## Likelihood Explanation
**High**. The exploit requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `pool.addLiquidity(owner = <allowlisted_address>, ...)` directly. The only prerequisite is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping or from on-chain `AllowedToDepositSet` events. [5](#0-4) 

## Recommendation
Validate `sender` (the actual caller) instead of, or in addition to, `owner`, mirroring the `SwapAllowlistExtension` pattern:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender]   // ← check the actual caller
        && !allowedDepositor[msg.sender][owner])   // optionally also gate owner
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is strictly "only allowlisted addresses may initiate deposits", checking `sender` alone is sufficient.

## Proof of Concept
**Setup**
- Pool `P` is deployed with `DepositAllowlistExtension` as a `beforeAddLiquidity` hook.
- Pool admin calls `setAllowedToDeposit(P, alice, true)`.
- `bob` is **not** on the allowlist.

**Attack**
```solidity
// Bob calls addLiquidity directly, setting owner = alice
pool.addLiquidity(
    alice,          // owner  ← allowlisted; extension check passes
    salt,
    deltas,
    callbackData,   // callback fires on bob (msg.sender), bob pays tokens
    extensionData
);
```

**Trace**
1. Pool calls `extension.beforeAddLiquidity(bob /*sender*/, alice /*owner*/, ...)`.
2. Extension discards `bob` (first param unnamed).
3. Checks `allowedDepositor[pool][alice]` → `true` → no revert.
4. Pool proceeds; `LiquidityLib.addLiquidity` credits shares to `(alice, salt)`.
5. Callback fires on `bob`; `bob` transfers tokens to the pool.

**Result**: `bob` (unauthorized) has deposited into a restricted pool. `alice` holds LP shares she never requested. The allowlist guard is fully bypassed. [2](#0-1) [6](#0-5)

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
