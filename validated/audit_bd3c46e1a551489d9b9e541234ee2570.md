Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks LP Recipient (`owner`) Instead of Actual Depositor (`sender`), Enabling Complete Allowlist Bypass — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the address that actually calls `addLiquidity` and pays tokens) and instead validates `owner` (a free caller-supplied argument designating the LP share recipient). Because any caller can supply any allowlisted address as `owner`, the deposit allowlist is rendered completely inoperative against any unprivileged address.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` into `_beforeAddLiquidity`:

```solidity
// MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both addresses to the extension hook:

```solidity
// ExtensionCalling.sol L95-98
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then drops `sender` entirely and checks only `owner`:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The first `address` parameter (`sender`) is unnamed and never read. The guard asks "is the LP-position recipient allowlisted?" rather than "is the depositor allowlisted?" — the exact opposite of the contract's own NatSpec: *"Gates `addLiquidity` by depositor address."*

`SwapAllowlistExtension.beforeSwap` demonstrates the correct pattern, reading `sender` and ignoring the recipient:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

**Exploit path:**
1. Admin deploys pool with `DepositAllowlistExtension`; sets `allowedDepositor[pool][trustedLP] = true`; attacker is not allowlisted.
2. Attacker calls `pool.addLiquidity(owner=trustedLP, salt=0, deltas=<chosen bins>, ...)`.
3. Hook receives `(sender=attacker, owner=trustedLP, ...)`. Guard evaluates `allowedDepositor[pool][trustedLP] == true` → passes.
4. Attacker pays tokens via `metricOmmSwapCallback`; LP shares are minted to `trustedLP`.
5. Attacker repeats across chosen bins to shift `curPosInBin`/`curBinIdx`, then swaps at the distorted price for profit.

No existing guard prevents this: `removeLiquidity` enforces `msg.sender == owner` (L206), but `addLiquidity` has no such check, and the extension hook is the sole access-control gate for deposits.

## Impact Explanation

The deposit allowlist guard is completely bypassed. Any unprivileged EOA or contract can add liquidity to any pool the admin intended to restrict (KYC-gated, whitelist-only, manipulation-resistant). Concrete consequences: (1) **Admin-boundary break** — an unprivileged path circumvents a pool-admin-configured access control; (2) **Pool-state manipulation** — attacker shifts `curPosInBin`/`curBinIdx` by depositing at chosen bins, then executes swaps at the distorted price, extracting value from existing LPs; (3) **Forced LP positions** — attacker creates LP shares attributed to an allowlisted address that never consented, potentially locking that address into an unwanted position.

## Likelihood Explanation

Exploitation requires no special privilege, no flash loan, and no complex setup. The allowlisted addresses are publicly readable from `allowedDepositor`. Any EOA can call `pool.addLiquidity(trustedLP, ...)` directly. The attack is repeatable at will.

## Recommendation

Replace the `owner` check with a `sender` check, mirroring `SwapAllowlistExtension`:

```solidity
// AFTER (correct)
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

```
Setup:
  pool  = MetricOmmPool with DepositAllowlistExtension configured
  admin calls setAllowedToDeposit(pool, trustedLP, true)
  attacker address is NOT in allowedDepositor

Attack:
  attacker calls pool.addLiquidity(
      owner        = trustedLP,      // allowlisted — passes the broken guard
      salt         = 0,
      deltas       = <chosen bins>,
      callbackData = ...,
      extensionData= ""
  )

Hook execution (DepositAllowlistExtension.beforeAddLiquidity):
  sender = attacker  (unnamed, ignored)
  owner  = trustedLP
  allowedDepositor[pool][trustedLP] == true  → guard passes

Result:
  - attacker pays tokens via metricOmmSwapCallback
  - LP shares minted to trustedLP
  - attacker successfully deposited into a restricted pool without being allowlisted
  - attacker repeats with targeted bins to manipulate curPosInBin/curBinIdx,
    then swaps at distorted price for profit

Foundry test skeleton:
  function test_depositAllowlistBypass() public {
      // configure pool with DepositAllowlistExtension
      // setAllowedToDeposit(pool, trustedLP, true)
      // vm.prank(attacker)
      // pool.addLiquidity(trustedLP, 0, deltas, callbackData, "")
      // assert: no revert, attacker bypassed allowlist
  }
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-196)
```text
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
