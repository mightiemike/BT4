Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` gates LP-share recipient (`owner`) instead of the token payer (`sender`), allowing any caller to bypass the deposit allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` receives the actual token payer as its first `address` argument (`sender`, forwarded by the pool as `msg.sender`) but leaves it unnamed and never reads it. Instead, it gates only `owner` — the LP-share recipient — which the caller freely controls. Any non-allowlisted EOA or contract can bypass the gate by naming an allowlisted address as `owner`, causing LP shares to be minted to that address while tokens are pulled from the attacker.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as the first argument to the extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments:

```solidity
// metric-core/contracts/ExtensionCalling.sol L95-98
_callExtensionsInOrder(
  BEFORE_ADD_LIQUIDITY_ORDER,
  abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
);
```

`DepositAllowlistExtension.beforeAddLiquidity` then discards the first argument entirely:

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

The allowlist mapping is keyed by `(pool, depositor)`:

```solidity
mapping(address pool => mapping(address depositor => bool)) public allowedDepositor;
```

Because `owner` is caller-controlled and the actual payer (`sender`) is never validated, the gate is structurally bypassed. The `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` overload (L56-68) further enables the owner/payer split without any additional barrier — the adder stores `msg.sender` as `payer` in transient storage for the callback but passes the caller-supplied `owner` directly to the pool, which the extension then checks.

## Impact Explanation

A pool configured with `DepositAllowlistExtension` is intended to be a private liquidity venue. The bypass breaks this invariant:

- A non-allowlisted attacker mints LP shares to an allowlisted address they control (e.g., a second wallet), then calls `removeLiquidity` from that address to recover tokens — effectively depositing into a restricted pool with no restriction.
- Even without controlling the `owner` address, the attacker force-injects liquidity, diluting existing LPs' share of fees and potentially disrupting any stop-loss or oracle-guard extension that relies on per-share metrics.

This constitutes broken core pool access-control with direct LP-asset impact (unauthorized share minting, fee dilution).

## Likelihood Explanation

- Requires no special privilege — any EOA or contract can call `addLiquidity` directly on the pool or route through `MetricOmmPoolLiquidityAdder`.
- Allowlisted addresses are publicly visible on-chain via `AllowedToDepositSet` events.
- The attack is repeatable at any time with no preconditions beyond knowing one allowlisted address.

## Recommendation

Change `beforeAddLiquidity` to check the first parameter (the actual token payer/caller) rather than — or in addition to — `owner`:

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

If the intent is to restrict both who pays and who receives shares, check both `sender` and `owner`.

## Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` attached with `beforeAddLiquidity` order set.
2. Admin calls `setAllowedToDeposit(pool, allowlistedAddress, true)` — only `allowlistedAddress` is permitted.
3. Attacker (non-allowlisted contract implementing `metricOmmModifyLiquidityCallback`) calls:
   ```solidity
   pool.addLiquidity(allowlistedAddress, salt, delta, callbackData, extensionData);
   ```
4. Pool calls `extension.beforeAddLiquidity(attacker, allowlistedAddress, ...)`.
5. Extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → no revert.
6. LP shares are minted to `allowlistedAddress`; tokens are transferred from the attacker via callback.
7. If the attacker controls `allowlistedAddress`, they call `removeLiquidity` and recover the tokens — having successfully deposited into a restricted pool.
8. Alternatively, route through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, allowlistedAddress, ...)` — the adder stores `msg.sender` as payer but passes `allowlistedAddress` as `owner` to the pool, achieving the same bypass without needing to implement the callback.