Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates on caller-supplied `owner` instead of `sender`, allowing any address to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual `msg.sender` of `addLiquidity`, who provides tokens via the swap callback) and instead validates the caller-supplied `owner` parameter (the LP-position recipient). Because `owner` is a free parameter with no independent validation, any unprivileged address can bypass the allowlist by naming any allowlisted address as `owner`, completely nullifying the pool admin's deposit restriction mechanism.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as distinct arguments to `_beforeAddLiquidity`: [1](#0-0) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both to the extension hook: [2](#0-1) 

`DepositAllowlistExtension.beforeAddLiquidity` then silently drops `sender` (unnamed first `address`) and gates exclusively on `owner`: [3](#0-2) 

The contract's own `isAllowedToDeposit` view names the second argument `depositor`, confirming the design intent is to gate the token-providing caller, not the position recipient: [4](#0-3) 

Because `owner` is a free caller-supplied parameter with no other on-chain validation, any address can pass the allowlist check by supplying any allowlisted address as `owner`. The wrong value gated is `allowedDepositor[msg.sender][owner]` — it should be `allowedDepositor[msg.sender][sender]`.

## Impact Explanation
The deposit allowlist is the pool admin's primary mechanism for restricting who may provide liquidity to a pool. The bug renders it completely ineffective: an unprivileged address can deposit into a restricted pool, breaking the admin-boundary invariant. This qualifies as **"Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path"** and **"Broken core pool functionality causing loss of funds or unusable liquidity flows"** — the LP position is credited to the allowlisted `owner`, but the tokens are provided by the non-allowlisted attacker, meaning the restriction on who can inject capital into the pool is fully bypassed.

## Likelihood Explanation
Exploitation requires no special privileges, no flash loan, and no oracle manipulation. Any EOA or contract can call `addLiquidity` with `owner = any_allowlisted_address`. The only precondition is knowledge of at least one allowlisted address (e.g., the pool admin themselves, which is typically discoverable on-chain via emitted `AllowedToDepositSet` events). The attack is repeatable at will.

## Recommendation
Replace the unnamed first parameter with `sender` and gate on it:

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

## Proof of Concept
1. Pool admin deploys a pool with `DepositAllowlistExtension` and calls `setAllowedToDeposit(pool, alice, true)`. Bob is **not** allowlisted.
2. Bob calls `pool.addLiquidity(owner = alice, salt = 0, deltas = ..., callbackData = ..., extensionData = ...)`.
3. Pool calls `_beforeAddLiquidity(msg.sender = bob, owner = alice, ...)`, which calls `extension.beforeAddLiquidity(bob, alice, ...)`.
4. Extension evaluates `allowedDepositor[pool][alice]` → `true` → no revert.
5. `LiquidityLib.addLiquidity` executes; Bob's callback transfers tokens into the pool; the LP position is credited to `alice`.
6. Bob has deposited into a restricted pool without being on the allowlist. The allowlist guard is fully bypassed.

A minimal Foundry test: deploy pool with `DepositAllowlistExtension`, allowlist only `alice`, call `addLiquidity` from `bob` with `owner = alice`, assert no revert and that `amount0Added > 0`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-98)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L28-30)
```text
  function isAllowedToDeposit(address pool_, address depositor) external view returns (bool) {
    return allowAllDepositors[pool_] || allowedDepositor[pool_][depositor];
  }
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
