Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Unlisted Addresses to Bypass the Deposit Guard — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, who provides tokens via the liquidity callback) and validates only `owner` (the LP position recipient). Any address not on the allowlist can bypass the guard by calling `addLiquidity` with a listed address as `owner`, depositing tokens into a pool that was explicitly configured to restrict depositors.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as separate arguments to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension hook: [2](#0-1) 

Inside `DepositAllowlistExtension.beforeAddLiquidity`, the first parameter (`sender`) is unnamed and therefore silently discarded. Only `owner` is checked against `allowedDepositor`: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly names and checks `sender` (the actual swapper), discarding `recipient`: [4](#0-3) 

The asymmetry is the root cause: the deposit guard never evaluates the address that actually provides tokens. The `allowedDepositor` mapping is keyed by depositor address, but the depositor (`sender`) is never read. [5](#0-4) 

## Impact Explanation

An unlisted address (`sender`) calls `pool.addLiquidity(owner=listed_address, ...)`. The extension checks `allowedDepositor[pool][listed_address]`, which passes. The pool then executes the liquidity callback against `msg.sender` (the unlisted address), who transfers tokens into the pool. The unlisted address successfully deposits into a pool whose admin explicitly configured a deposit allowlist to prevent exactly this. The deposit allowlist — a core admin-configured security boundary — is rendered entirely ineffective for controlling the actual token provider. This directly matches the "Allowlist path: deposit/swap allowlist checks must cover the exact actor/action intended and cannot be bypassed through owner/salt separation" criterion.

## Likelihood Explanation

The bypass requires only that the attacker knows one allowlisted address, which is trivially discoverable by reading `AllowedToDepositSet` events or calling `allowedDepositor` directly. No special role, flash loan, privileged access, or complex setup is needed. Any EOA or contract can execute the bypass in a single transaction, and it is repeatable indefinitely.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
function beforeAddLiquidity(address sender, address, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is to restrict both the depositor and the owner, both should be checked independently.

## Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension E configured with BEFORE_ADD_LIQUIDITY_ORDER pointing to E.
  Alice (0xAlice) is allowlisted: allowedDepositor[P][0xAlice] = true
  Bob   (0xBob)  is NOT allowlisted

Attack:
  Bob calls P.addLiquidity(owner=0xAlice, salt, deltas, callbackData, extensionData)

  Pool calls E.beforeAddLiquidity(sender=0xBob, owner=0xAlice, ...)
    → sender (0xBob) is unnamed, discarded
    → check: allowedDepositor[P][0xAlice] == true  ✓  (passes)

  Pool calls LiquidityLib.addLiquidity(owner=0xAlice, ...)
    → liquidity callback fires on 0xBob; Bob transfers tokens to pool
    → Alice receives LP shares

Result:
  Bob (unlisted) successfully deposited tokens into the allowlisted pool.
  The deposit allowlist guard was never evaluated against the actual depositor.
```

A Foundry test can confirm this by deploying the extension, allowlisting Alice, then calling `addLiquidity` from Bob with `owner=Alice` and asserting the call succeeds and tokens are transferred from Bob.

### Citations

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
