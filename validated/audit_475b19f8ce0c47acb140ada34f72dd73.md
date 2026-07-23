Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Validates `owner` Instead of `sender`, Allowing Any Caller to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and validates `owner` (the LP-position recipient, a caller-supplied value) against the allowlist. Because `addLiquidity` accepts an arbitrary `owner` address with no constraint, any unpermissioned address can bypass the allowlist by supplying an allowlisted address as `owner`. The extension's own admin-facing setter names the restricted party `depositor`, and the analogous `SwapAllowlistExtension.beforeSwap` correctly validates `sender`, confirming the deposit hook checks the wrong entity.

## Finding Description
`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP-position recipient into the hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
``` [1](#0-0) 

`owner` is completely unconstrained — any address may be passed. The hook then ignores `sender` entirely and validates only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [2](#0-1) 

The admin-facing setter names the restricted party `depositor`, signalling intent to gate the calling party: [3](#0-2) 

By contrast, `SwapAllowlistExtension.beforeSwap` correctly validates `sender`: [4](#0-3) 

**Exploit path:**
1. Pool `P` has `DepositAllowlistExtension` configured; `alice` is allowlisted, `bob` is not.
2. `bob` calls `P.addLiquidity(owner=alice, salt=0, deltas=<valid>, callbackData=<bob pays>, extensionData="")`.
3. Hook receives `(sender=bob, owner=alice)`, checks `allowedDepositor[P][alice] == true` → no revert.
4. Pool invokes bob's callback; bob transfers tokens to the pool.
5. `alice` receives LP shares for a position she never initiated; bob has deposited into a restricted pool.

The `removeLiquidity` path enforces `msg.sender == owner` (line 206), so bob cannot withdraw alice's shares — but the deposit restriction is fully defeated. [5](#0-4) 

## Impact Explanation
The allowlist is completely bypassed: any unpermissioned address can inject liquidity into a pool whose admin believed it was restricted. This breaks the core access-control guarantee of the extension, renders KYC/AML-gated institutional pools non-compliant, and allows forced LP-position creation on allowlisted addresses without their consent. Because Metric OMM bins are oracle-anchored, injected liquidity can also shift `curBinIdx`/`curPosInBin`, altering the effective spread seen by subsequent swappers and potentially extracting value from existing LPs. This constitutes broken core pool functionality causing loss of funds and unusable access-control flows, meeting the contest's High/Medium threshold.

## Likelihood Explanation
`addLiquidity` is a public, permissionless entry point requiring no special role or token. The attacker only needs one allowlisted address, trivially discoverable from past `AllowedToDepositSet` events. No flash loan, oracle manipulation, or multi-step setup is required. The bypass is deterministic and repeatable every block.

## Recommendation
Change `beforeAddLiquidity` to validate `sender` (the actual caller) instead of `owner`, consistent with `SwapAllowlistExtension.beforeSwap`:

```solidity
// AFTER (correct entity checked)
function beforeAddLiquidity(address sender, address /*owner*/, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intended semantic is genuinely "restrict which addresses may *own* positions," a separate `sender` check must also be added to prevent token injection by unauthorized callers, and all documentation and the admin setter must be updated accordingly.

## Proof of Concept

```
Setup:
  Pool P has DepositAllowlistExtension E configured.
  Admin calls E.setAllowedToDeposit(P, alice, true).
  bob is NOT in the allowlist.

Attack:
  bob calls P.addLiquidity(
      owner        = alice,   // allowlisted → hook passes
      salt         = 0,
      deltas       = <any valid bin deltas>,
      callbackData = <bob's callback pays the tokens>,
      extensionData = ""
  );

Result:
  E.beforeAddLiquidity(sender=bob /*ignored*/, owner=alice, ...)
    checks allowedDepositor[P][alice] == true → no revert.
  Pool calls bob's callback; bob transfers tokens to pool.
  alice receives LP shares for a position she never initiated.
  bob has successfully deposited into a pool he is not permitted to access.

Verification:
  Assert bob's address is NOT in allowedDepositor[P].
  Assert the deposit succeeds without revert.
  Assert alice's position shares increased.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L18-21)
```text
  function setAllowedToDeposit(address pool_, address depositor, bool allowed) external onlyPoolAdmin(pool_) {
    allowedDepositor[pool_][depositor] = allowed;
    emit AllowedToDepositSet(pool_, depositor, allowed);
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
