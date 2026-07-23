Audit Report

## Title
Deposit Allowlist Gates on LP Position `owner` Instead of Token-Providing `sender`, Enabling Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual token-providing caller) and gates access solely on `owner` (the LP position recipient). Because `MetricOmmPool.addLiquidity` imposes no constraint that `msg.sender == owner`, any address excluded from the allowlist can bypass the guard by nominating any allowlisted address as `owner` while itself supplying the tokens and triggering the swap callback.

## Finding Description
`MetricOmmPool.addLiquidity` passes two distinct actors to the extension hook:

```solidity
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`msg.sender` is the caller who will supply tokens via the swap callback; `owner` is the address that will receive the LP position. `addLiquidity` enforces no constraint that these two are the same — only `removeLiquidity` does:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both actors to the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

`DepositAllowlistExtension.beforeAddLiquidity` then discards `sender` (unnamed first parameter) and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [4](#0-3) 

The allowlist mapping is keyed on `depositor`, confirming `sender` is the intended subject of the check: [5](#0-4) 

The contract's own NatDoc states it "Gates `addLiquidity` by **depositor** address," confirming the intent is to restrict the token-providing caller, not the position recipient. [6](#0-5) 

## Impact Explanation
The pool admin's access-control gate is fully circumvented: any address excluded from the allowlist can deposit tokens into a restricted pool by setting `owner` to any allowlisted address. The pool receives tokens from an actor the admin explicitly excluded, breaking the invariant that only approved depositors contribute liquidity. Additionally, the allowlisted `owner` receives LP shares they never requested, which may interact adversely with other extensions (e.g., `OracleValueStopLossExtension` watermarks updated for bins touched by the forced deposit). This constitutes an admin-boundary break with direct pool-state impact.

## Likelihood Explanation
The attack requires no special privilege. Any EOA or contract can call `addLiquidity` with an arbitrary `owner`. The only precondition is knowing one allowlisted address, which is readable from the public `allowedDepositor` mapping. The bypass is deterministic and repeatable with zero cost beyond gas.

## Recommendation
Name and use `sender` in the guard instead of `owner`:

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
1. Pool admin calls `setAllowedToDeposit(pool, Alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(owner = Alice, salt = 0, deltas = ..., callbackData = ..., extensionData = "")`.
3. Pool invokes `DepositAllowlistExtension.beforeAddLiquidity(sender = Bob, owner = Alice, ...)`.
4. Guard evaluates `!allowedDepositor[pool][Alice]` → `false` → **no revert**.
5. `LiquidityLib.addLiquidity` executes; Bob's callback supplies tokens; Alice receives LP shares.
6. Bob has deposited into a pool he is explicitly excluded from, and Alice holds an LP position she never initiated.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-12)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
contract DepositAllowlistExtension is BaseMetricExtension, IDepositAllowlistExtension {
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L13-13)
```text
  mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
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
