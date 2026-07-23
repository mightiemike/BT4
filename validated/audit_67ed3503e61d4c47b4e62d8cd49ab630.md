Looking at the actual code to verify the claim:

Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Position `owner` Instead of Actual Depositor, Enabling Full Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the first argument (the actual depositor address passed by the pool) and instead gates on `owner`, the position recipient. Because `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` accepts any caller-specified `owner`, any unprivileged actor can bypass the deposit allowlist by supplying an already-allowlisted address as `owner` while paying the tokens themselves, rendering the allowlist entirely ineffective.

## Finding Description
The allowlist mapping is keyed `allowedDepositor[pool][depositor]`. In `beforeAddLiquidity`, `msg.sender` is the pool (correct key for the pool dimension) and the second positional argument `owner` is used as the depositor key — but `owner` is the position recipient, not the actual caller of `pool.addLiquidity()`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, ...)
  external view override returns (bytes4)
{
  if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
  }
  ...
}
```

The pool passes the actual depositor as the first (discarded) argument:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` (the explicit-owner overload) accepts any non-zero `owner` from the caller and uses `msg.sender` as the payer:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
function addLiquidityExactShares(address pool, address owner, ...) external payable override {
  _validateOwner(owner);  // only checks != address(0)
  ...
  return _addLiquidity(pool, owner, salt, deltas, msg.sender, ...);
}
```

**Exploit path:**
1. Attacker (not on allowlist) calls `addLiquidityExactShares(pool, allowlistedAddress, salt, deltas, max0, max1, extensionData)`.
2. `_addLiquidity` calls `pool.addLiquidity(allowlistedAddress, ...)`.
3. Pool calls `_beforeAddLiquidity(MetricOmmPoolLiquidityAdder, allowlistedAddress, ...)`.
4. Extension evaluates `allowedDepositor[pool][allowlistedAddress]` → `true` → hook passes.
5. Attacker's tokens are pulled via callback; LP shares are minted to `allowlistedAddress`.

The same bypass is reachable by calling `pool.addLiquidity()` directly with a custom callback contract, naming any allowlisted address as `owner`. The `_validateOwner` check only rejects `address(0)`, imposing no allowlist constraint on the caller.

## Impact Explanation
The deposit allowlist — an admin-configured access control — is completely ineffective. Any unprivileged actor can deposit into a restricted pool by naming an allowlisted address as the position owner. This is a broken admin-boundary control: the pool admin's restriction on who may deposit is bypassed by an unprivileged path through the periphery router. The LP shares are credited to the named allowlisted address (not the attacker), so the attacker does not directly extract funds, but the invariant that only allowlisted depositors may add liquidity is fully broken.

## Likelihood Explanation
Exploitation requires no special privileges, no price manipulation, and no flash loan. Any EOA or contract can call `addLiquidityExactShares` with an allowlisted address as `owner`. The attacker only needs to know one allowlisted address (publicly readable via `allowedDepositor`) and hold sufficient tokens. The attack is repeatable at will.

## Recommendation
Replace the discarded first argument with a named parameter and check it instead of `owner`:

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

This ensures the check applies to the actual depositor (the address that called `pool.addLiquidity()`) rather than the position recipient.

## Proof of Concept
```solidity
// Foundry test sketch
function test_allowlistBypass() public {
  // Setup: pool with DepositAllowlistExtension; only `allowedLP` is allowlisted
  address allowedLP = makeAddr("allowedLP");
  extension.setAllowedToDeposit(pool, allowedLP, true);

  // Attacker is NOT on the allowlist
  address attacker = makeAddr("attacker");
  deal(token0, attacker, 1e18);
  deal(token1, attacker, 1e18);

  vm.startPrank(attacker);
  IERC20(token0).approve(address(liquidityAdder), type(uint256).max);
  IERC20(token1).approve(address(liquidityAdder), type(uint256).max);

  // Attacker names allowedLP as owner — bypass succeeds, no revert
  liquidityAdder.addLiquidityExactShares(
    pool, allowedLP, 0, deltas, 1e18, 1e18, ""
  );
  vm.stopPrank();

  // LP shares credited to allowedLP, attacker paid — allowlist defeated
}
```