Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks Caller-Controlled `owner` Instead of `sender`, Enabling Full Allowlist Bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter and validates `owner` — a free, caller-supplied argument to `addLiquidity` — against the allowlist. Because any caller can set `owner` to any address, including one already on the allowlist, the deposit gate is trivially bypassed by any unprivileged address. The actual token provider (`sender` / `msg.sender` of `addLiquidity`) is never checked.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` argument as `owner` to `_beforeAddLiquidity`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L191
_beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

`ExtensionCalling._beforeAddLiquidity` forwards both values faithfully to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol L97
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
```

`DepositAllowlistExtension.beforeAddLiquidity` then discards `sender` (unnamed first parameter) and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-L38
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

Since `owner` is a free parameter supplied by the caller of `addLiquidity`, any address can pass `owner = <any allowlisted address>` and the check evaluates `allowedDepositor[pool][allowlistedAddress] == true`, returning success. The actual depositing address is never evaluated. `SwapAllowlistExtension.beforeSwap` correctly gates `sender` instead, confirming the asymmetry is a defect:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

## Impact Explanation

Any pool deploying `DepositAllowlistExtension` to restrict liquidity provision is rendered completely open. An unprivileged caller deposits tokens into the pool (their tokens are pulled via the modify-liquidity callback from `msg.sender`) while the allowlisted `owner` receives LP shares they did not request. The pool admin's access control over who may provide liquidity is nullified: the pool's private LP set is contaminated with unauthorized liquidity, altering its composition and risk profile without consent. This constitutes broken core pool functionality causing unauthorized fund injection — a direct impact on pool integrity matching the "Allowlist path" pivot.

## Likelihood Explanation

- No special privileges required: any EOA or contract can call `addLiquidity` on a pool.
- The only precondition is knowing one allowlisted address, which is discoverable from on-chain `AllowedToDepositSet` events emitted by `setAllowedToDeposit`.
- The bypass is structural and permanent for every pool registering this extension.
- Fully repeatable with arbitrary deposit amounts.

## Recommendation

Replace the `owner` check with a `sender` check, consistent with `SwapAllowlistExtension`:

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

```
Setup:
  - Pool P deployed with DepositAllowlistExtension E.
  - Admin calls E.setAllowedToDeposit(P, Alice, true).
  - Bob is NOT in the allowlist.

Attack:
  1. Bob calls P.addLiquidity({ owner: Alice, salt: 0, deltas: ..., ... }).
  2. MetricOmmPool calls _beforeAddLiquidity(Bob /*msg.sender*/, Alice /*owner*/, ...).
  3. ExtensionCalling encodes and dispatches to E.beforeAddLiquidity(Bob, Alice, ...).
  4. Hook evaluates: allowedDepositor[P][Alice] == true → passes, no revert.
  5. Bob's tokens are pulled from Bob via the modify-liquidity callback.
  6. Alice receives LP shares she did not request.

Result:
  - Bob deposited into a pool he is not authorized to access.
  - The deposit allowlist is fully bypassed with zero privileges.
  - Pool's private LP set is contaminated with unauthorized liquidity.

Foundry test sketch:
  vm.prank(bob);
  pool.addLiquidity(alice, 0, deltas, callbackData, "");
  // Expect: succeeds despite bob not being allowlisted
  // Actual: passes because allowedDepositor[pool][alice] == true
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

**File:** metric-core/contracts/MetricOmmPool.sol (L188-195)
```text
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
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
