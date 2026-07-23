Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Position `owner` Instead of Actual Depositor `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual depositor/payer, i.e. `msg.sender` of `addLiquidity`) and gates access only on `owner` (the LP position recipient, a caller-supplied argument). Because `addLiquidity` imposes no restriction requiring `msg.sender == owner`, any address not on the allowlist can bypass the guard by naming any allowlisted address as the LP position `owner`. This completely defeats the deposit allowlist access control configured by pool admins.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as two distinct arguments to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

There is no `msg.sender == owner` restriction in `addLiquidity` (unlike `removeLiquidity` at L206 which enforces `if (msg.sender != owner) revert NotPositionOwner()`).

`DepositAllowlistExtension.beforeAddLiquidity` receives both addresses but silently drops `sender` (first argument is unnamed) and checks only `owner`:

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

The storage mapping `allowedDepositor[pool][depositor]` and the setter `setAllowedToDeposit(pool, depositor, ...)` make the intent clear: the guard is meant to restrict **who deposits** (the `sender`), not who receives LP shares. The analogous `SwapAllowlistExtension.beforeSwap` correctly checks `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Exploit path:
1. Pool is configured with `DepositAllowlistExtension`; `allowedDepositor[pool][alice] = true`; `bob` is NOT on the allowlist.
2. `bob` calls `pool.addLiquidity(alice, salt, deltas, callbackData, "")`.
3. Extension evaluates `allowedDepositor[pool][alice]` → `true` → guard passes.
4. `bob` pays tokens via the modify-liquidity callback; `alice` receives LP shares.
5. `bob` has deposited into a restricted pool without being on the allowlist.

## Impact Explanation
This is an **admin-boundary break**: a pool admin-configured access control (e.g., KYC/compliance gate, regulated pool restriction) is bypassed by any unprivileged address. Any allowlisted address (including the pool admin or any known KYC'd participant) serves as the bypass key. An unauthorized actor can freely add liquidity to a restricted pool, altering bin depths and affecting oracle-anchored swap pricing for all users. Additionally, LP shares can be forced onto allowlisted addresses without their consent.

## Likelihood Explanation
No special privileges, flash loans, or complex setup are required — a single direct call to `addLiquidity` with any allowlisted address as `owner` suffices. The pool's `addLiquidity` signature explicitly separates `owner` from `msg.sender` with no equality restriction, making this trivially exploitable by any external caller who knows any allowlisted address (e.g., the pool admin, which is typically discoverable on-chain).

## Recommendation
Change `beforeAddLiquidity` to check `sender` (the actual depositor/payer) instead of `owner`, mirroring the correct pattern in `SwapAllowlistExtension.beforeSwap`:

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
```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][alice] = true
  bob is NOT on the allowlist

Attack:
  bob calls pool.addLiquidity(
      owner = alice,   // allowlisted address
      salt  = 0,
      deltas = <valid bin deltas>,
      callbackData = ...,
      extensionData = ""
  )

Extension check (DepositAllowlistExtension.beforeAddLiquidity):
  sender arg (bob) is silently discarded
  allowedDepositor[pool][alice] == true  →  guard passes

Result:
  bob pays tokens via metricOmmModifyLiquidityCallback
  alice receives LP shares
  bob has deposited into a restricted pool without being on the allowlist
  the deposit allowlist is fully bypassed
```

Foundry test outline:
1. Deploy pool with `DepositAllowlistExtension`.
2. Call `setAllowedToDeposit(pool, alice, true)` as pool admin.
3. Call `pool.addLiquidity(alice, 0, deltas, callbackData, "")` from `bob` (not allowlisted).
4. Assert the call succeeds and `bob` paid tokens while `alice` received LP shares.