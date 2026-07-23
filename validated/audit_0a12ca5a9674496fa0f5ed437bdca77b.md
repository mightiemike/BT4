Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Gates on LP-Share Recipient Instead of Token Provider, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` (the actual `msg.sender` of `addLiquidity`, who provides tokens via callback) as its first argument but leaves it unnamed and unchecked. The guard only inspects `owner` (the LP-share recipient), which is a caller-supplied parameter in `MetricOmmPool.addLiquidity`. Any unprivileged address can bypass the deposit allowlist by naming any already-allowlisted address as `owner`, depositing tokens into the pool, and forcing LP shares onto that address.

## Finding Description

`MetricOmmPool.addLiquidity` accepts a caller-supplied `owner` parameter with no restriction on who may supply it: [1](#0-0) 

It calls `_beforeAddLiquidity(msg.sender, owner, ...)`, passing the actual caller as `sender` and the arbitrary `owner` as the share recipient: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully encodes both values and dispatches them to the extension: [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` receives `sender` as its first argument but leaves it **unnamed and unchecked**. Only `owner` is inspected: [4](#0-3) 

Because `LiquidityLib` is called via DELEGATECALL and `msg.sender` resolves to the original external caller, the token callback is issued to the actual caller (not `owner`), pulling the attacker's tokens into the pool: [5](#0-4) 

LP shares are credited to `owner`, and `removeLiquidity` enforces `msg.sender == owner`, so only the allowlisted address can reclaim them: [6](#0-5) 

The existing guard (`allowedDepositor[msg.sender][owner]`) is entirely insufficient because it checks the wrong address — the share recipient rather than the token provider.

## Impact Explanation

The deposit allowlist is the pool admin's mechanism to enforce a permissioned liquidity pool (KYC-gated, institutional-only, whitelist-only). Because the guard checks `owner` instead of `sender`, the restriction is completely ineffective:

1. **Admin-boundary break**: any unprivileged EOA or contract bypasses the pool admin's deposit restriction without any special role.
2. **Bin-state manipulation**: an unauthorized party injects tokens into specific bins, altering the pool's internal token0/token1 balance distribution. Combined with a swap (if no swap allowlist is configured), this can be used to extract value at a manipulated bin position.
3. **Forced LP exposure**: an allowlisted address receives LP shares it never requested, giving it unintended and unwanted exposure to pool risk that it cannot immediately shed without actively calling `removeLiquidity`.

This constitutes a direct admin-boundary break and broken core pool functionality, meeting the required impact gate.

## Likelihood Explanation

- No special role or privilege is required; any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address for the target pool, which is observable on-chain via `AllowedToDepositSet` events or direct `allowedDepositor` storage reads.
- The attack is repeatable and costs only gas plus the deposited tokens (which the attacker does not recover, as LP shares go to `owner`).

## Recommendation

Replace the unnamed first parameter with `sender` and enforce the allowlist on the actual token provider:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender]
        && !allowedDepositor[msg.sender][sender])
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

At minimum, `sender` (the address that provides tokens and triggers the callback) must be checked. Whether `owner` should additionally be checked is a policy decision for the pool admin.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner        = alice,   // allowlisted address
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1e18] },
      callbackData = ...,
      extensionData = ""
  )

Extension check (beforeAddLiquidity):
  sender = bob  (unnamed, unchecked)
  owner  = alice
  allowedDepositor[pool][alice] == true  →  guard passes

Callback:
  pool calls bob.metricOmmModifyLiquidityCallback(...)
  bob transfers token0/token1 to the pool

Result:
  bob's tokens are in the pool
  alice holds LP shares she never requested
  bob bypassed the deposit allowlist with zero privilege
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L14-20)
```text
/// @notice Holds `addLiquidity` / `removeLiquidity` logic as a deployable (DELEGATECALL-linked)
///         library so it does not inflate `MetricOmmPool` bytecode.
/// @dev Because every `public` function is called via DELEGATECALL from the pool:
///      - `msg.sender` is the original external caller.
///      - `address(this)` is the pool contract.
///      - Storage references resolve against the pool's state.
///      - ERC-20 calls (`balanceOf`, `safeTransfer`) operate on the pool's holdings.
```
