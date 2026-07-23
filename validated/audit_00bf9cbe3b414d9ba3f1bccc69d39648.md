Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity` Checks `owner` Instead of `sender`, Allowing Any Address to Bypass the Deposit Allowlist — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently discards the `sender` parameter (the actual caller who pays tokens) and instead checks `owner` (the LP position recipient). Because `sender` and `owner` can differ in `addLiquidity`, any unprivileged address can bypass the deposit allowlist by specifying an allowlisted `owner`. The deposit allowlist — the sole mechanism for restricting LP participation in gated pools — is entirely ineffective against the actual depositor/payer.

## Finding Description

`MetricOmmPool.addLiquidity` passes `msg.sender` as `sender` and the caller-supplied `owner` as a distinct parameter to `_beforeAddLiquidity`: [1](#0-0) 

`IMetricOmmExtensions.beforeAddLiquidity` explicitly names both parameters: [2](#0-1) 

`DepositAllowlistExtension` overrides this function but silently drops `sender` (unnamed `address,`) and checks `owner` instead: [3](#0-2) 

`msg.sender` in the extension is the pool (correct key for the mapping), but `owner` is the position recipient — not the address that pays tokens. The `SwapAllowlistExtension` correctly checks `sender` for the analogous swap gate, confirming the intended pattern: [4](#0-3) 

The periphery router `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly supports `owner != msg.sender`, making the split trivially reachable with no special privileges: [5](#0-4) 

## Impact Explanation

A pool admin deploys a restricted pool with `DepositAllowlistExtension` to gate which addresses may provide liquidity. Any unprivileged address can bypass this gate by calling `addLiquidityExactShares` with an allowlisted `owner`. The check passes because `owner` is allowlisted; the unauthorized `sender` pays the tokens; the position is credited to `owner`. The deposit allowlist — the sole mechanism for restricting LP participation — is entirely ineffective against the actual depositor/payer. This breaks a core pool access-control invariant and constitutes broken core pool functionality causing loss of the intended restriction on LP participation.

## Likelihood Explanation

The `sender ≠ owner` split is a first-class, documented feature of the pool interface and the periphery router exposes it directly via `addLiquidityExactShares(pool, owner, ...)`. No special privileges are required. Any address that knows one allowlisted owner address can exploit this immediately and repeatably.

## Recommendation

Replace the `owner` check with the `sender` parameter, consistent with `SwapAllowlistExtension`:

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

1. Pool admin creates a pool with `DepositAllowlistExtension` and allowlists only `alice` via `setAllowedToDeposit(pool, alice, true)`.
2. `bob` (not allowlisted) calls:
   ```solidity
   router.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "");
   ```
   Inside the router: `sender = bob` (payer), `owner = alice` (position recipient).
3. Pool calls `beforeAddLiquidity(bob, alice, ...)` on the extension.
4. Extension checks `allowedDepositor[pool][alice]` → `true`. No revert.
5. Pool calls back `bob` for payment. `bob` pays tokens. Position credited to `alice`.
6. `bob` has deposited into a pool he is not allowlisted for. The allowlist is bypassed.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
```

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L14-20)
```text
  function beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) external returns (bytes4);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```
