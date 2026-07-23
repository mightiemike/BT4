Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller who pays tokens via callback) and gates only on `owner` (the LP-position beneficiary). Any non-allowlisted address can bypass the deposit guard by calling `pool.addLiquidity(owner = allowlistedAddress, …)`, causing the hook to pass while the real depositor is never checked. The pool's curated-access invariant — "only allowlisted addresses may deposit" — is fully broken.

## Finding Description

The hook signature at [1](#0-0)  leaves the first `address` argument unnamed and checks only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the user-supplied `owner` as the position beneficiary: [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both arguments to the hook: [3](#0-2) 

Because the extension discards `sender` entirely, the check `allowedDepositor[pool][owner]` evaluates the beneficiary address, not the caller. The NatSpec at [4](#0-3)  states the contract "Gates `addLiquidity` by depositor address," but the implementation gates by the LP-position recipient instead.

**Exploit path:**
1. Pool admin configures `DepositAllowlistExtension`; `allowedDepositor[pool][Alice] = true`, `allowedDepositor[pool][Bob] = false`.
2. Bob (non-allowlisted) calls `pool.addLiquidity(owner = Alice, salt, deltas, callbackData, extensionData)`.
3. The hook evaluates `allowedDepositor[pool][Alice]` → `true` → no revert.
4. `LiquidityLib.addLiquidity` mints LP shares credited to Alice; Bob's callback pays `token0`/`token1`.
5. Bob has deposited into a curated pool he is explicitly excluded from; the allowlist is fully bypassed.

## Impact Explanation

A pool deploying `DepositAllowlistExtension` for KYC, compliance, or whitelist-only LP membership receives no protection against the actual payer. Any excluded address can alter pool state (change bin totals, mint LP shares to an allowlisted address) by nominating any allowlisted address as `owner`. This breaks the admin-boundary invariant: an unprivileged path defeats a configured access-control guard without any privileged action. The allowlisted address also receives unsolicited LP shares, which may itself be harmful depending on the pool's design. This constitutes a broken core pool functionality / admin-boundary break with direct fund-state impact above Sherlock thresholds.

## Likelihood Explanation

Exploitation requires no special privileges, no flash loan, and no oracle manipulation. The `allowedDepositor` mapping is public on-chain, so any attacker can enumerate allowlisted addresses. The attack is unconditional, repeatable by any EOA or contract, and requires only a single direct call to `pool.addLiquidity`.

## Recommendation

Gate on `sender` (the actual caller), not `owner`. Replace the unnamed first parameter:

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

If the design intent is to gate the LP-position owner rather than the payer, both must be checked independently, and the NatSpec must be corrected to reflect that intent.

## Proof of Concept

```
Setup:
  pool configured with DepositAllowlistExtension
  allowedDepositor[pool][Alice] = true
  allowedDepositor[pool][Bob]   = false

Attack:
  vm.prank(Bob);
  pool.addLiquidity(
      owner        = Alice,   // allowlisted — hook checks this, passes
      salt         = 0,
      deltas       = { binIdxs: [0], shares: [1000] },
      callbackData = "",
      extensionData = ""
  );
  // Bob's addLiquidityCallback pays token0/token1
  // Alice receives 1000 LP shares
  // Hook never evaluated Bob's address

Assert:
  Bob has deposited into a curated pool he is explicitly excluded from.
  Pool admin's allowlist protection is fully bypassed.
  allowedDepositor[pool][Bob] == false  // unchanged, yet Bob deposited
```

### Citations

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L11-11)
```text
/// @notice Gates `addLiquidity` by depositor address, per pool.
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
