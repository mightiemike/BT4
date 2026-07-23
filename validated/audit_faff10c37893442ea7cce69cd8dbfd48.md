Audit Report

## Title
Deposit Allowlist Checks `owner` Instead of `sender`, Allowing Any Unprivileged Address to Bypass the Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`) and gates access on `owner` instead — a free caller-supplied argument. Because any caller can nominate an already-allowlisted address as `owner`, the deposit allowlist is completely ineffective: any unprivileged address can deposit into a restricted pool, locking their tokens under the allowlisted address's position without that address's consent.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and a caller-supplied `owner` as two distinct arguments to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension hook via `abi.encodeCall`:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

However, `DepositAllowlistExtension.beforeAddLiquidity` discards the first parameter (`sender`) entirely — it is unnamed — and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-41
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

The contract's own NatSpec, the `allowedDepositor` mapping key name, and the `setAllowedToDeposit(address pool_, address depositor, ...)` setter all declare the intent is to gate by **depositor** (i.e., the actual caller):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L11-13
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
    mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

Since `owner` is freely chosen by the caller, any non-allowlisted address `bob` can pass the guard by setting `owner = alice` where `alice` is allowlisted. The check `allowedDepositor[pool][alice]` returns `true`, the guard passes, and `bob`'s tokens are deposited into the pool credited to `alice`'s position. Because `removeLiquidity` enforces `msg.sender == owner` (L206 of `MetricOmmPool.sol`), only `alice` can withdraw those shares — `bob`'s tokens are permanently locked under `alice`'s position without `alice`'s consent.

## Impact Explanation

The deposit allowlist is completely nullified. Any unprivileged caller can bypass the admin-configured access control by supplying any allowlisted address as `owner`. Concrete fund-impacting consequences:

1. **Admin-boundary break**: The pool admin's access control is bypassed by any unprivileged caller.
2. **Forced LP exposure**: The allowlisted `owner` receives unwanted shares in attacker-chosen bins (potentially illiquid or far-from-mid bins), distorting their risk profile without consent.
3. **Pool liquidity manipulation**: An attacker can concentrate liquidity in specific bins to skew the pool's effective spread and bin-crossing behavior, then profit via a subsequent swap (if no swap allowlist is active), extracting value from existing LPs.

The attacker's deposited tokens are locked under the victim's position, constituting a direct loss of the attacker's principal (irreversible) and forced illiquid LP exposure for the victim — meeting the Sherlock threshold for a High severity admin-boundary break with fund-impacting consequences.

## Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity`.
- The only prerequisite is knowing one allowlisted address, trivially discoverable from `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
- The attacker bears the cost of deposited tokens but can recover value through a subsequent swap if no `SwapAllowlistExtension` is active.
- Pools relying solely on `DepositAllowlistExtension` for access control are fully exposed.

## Recommendation

Replace the ignored first parameter with the actual `sender` check:

```solidity
function beforeAddLiquidity(
    address sender,   // ← use this, not owner
    address,
    uint80,
    LiquidityDelta calldata,
    bytes calldata
) external view override returns (bytes4) {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

This matches the stated contract invariant ("Gates `addLiquidity` by depositor address") and the `setAllowedToDeposit(address depositor, ...)` API.

## Proof of Concept

```solidity
// Setup: pool with DepositAllowlistExtension; only `alice` is allowlisted.
// `bob` is NOT allowlisted.

vm.startPrank(bob);
token0.approve(address(pool), type(uint256).max);
token1.approve(address(pool), type(uint256).max);

// Bob bypasses the allowlist by nominating alice as owner:
pool.addLiquidity(
    alice,       // owner — passes allowlist check (alice is allowlisted)
    0,           // salt
    deltas,      // attacker-chosen liquidity delta
    callbackData,
    ""
);
vm.stopPrank();

// Result: bob's tokens are now in alice's LP position.
// The allowlist check never evaluated bob's address.
assertEq(extension.isAllowedToDeposit(address(pool), bob), false); // bob is NOT allowed
// Yet the deposit succeeded — guard bypassed.
// alice has unwanted LP exposure; bob's tokens are locked under alice's position.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-13)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-20)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-195)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
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
