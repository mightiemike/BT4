Audit Report

## Title
`DepositAllowlistExtension.beforeAddLiquidity()` checks LP position `owner` instead of actual depositor `sender`, allowing allowlist bypass — (File: `metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

## Summary
`DepositAllowlistExtension.beforeAddLiquidity()` silently discards the `sender` parameter (the actual depositor/caller) and gates only on `owner` (the LP position recipient). Any non-allowlisted user can bypass the pool admin's deposit allowlist by routing through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner=allowlistedAddress, ...)`, specifying an allowlisted address as `owner` while paying the tokens themselves. This breaks the pool admin's curation policy without any privileged cooperation.

## Finding Description
`DepositAllowlistExtension.beforeAddLiquidity()` at L32–42 leaves the first `address` parameter (`sender`) unnamed and unused, checking only `owner`:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

`MetricOmmPool.addLiquidity()` passes `msg.sender` as `sender` and the caller-supplied `owner` as the LP position owner: [2](#0-1) 

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, ...)` explicitly supports a distinct `owner` from `msg.sender`, storing `msg.sender` as the token payer and passing `owner` to the pool: [3](#0-2) 

The internal `_addLiquidity` then calls `pool.addLiquidity(positionOwner, ...)` where `positionOwner` is the attacker-controlled `owner` argument, not the actual caller: [4](#0-3) 

The `_validateOwner` check only rejects `address(0)` and does not enforce that `owner == msg.sender`.

Exploit flow:
1. Pool admin calls `setAllowedToDeposit(pool, alice, true)`. Bob is not allowlisted.
2. Bob calls `liquidityAdder.addLiquidityExactShares(pool, alice, salt, deltas, max0, max1, "")`.
3. Adder stores `payer = bob`, calls `pool.addLiquidity(alice, ...)`.
4. Pool calls `_beforeAddLiquidity(sender=adder, owner=alice, ...)`.
5. Extension checks `allowedDepositor[pool][alice]` → `true` → passes.
6. Bob pays tokens via callback; Alice receives LP shares.

The `SwapAllowlistExtension.beforeSwap()` correctly checks `sender` (the actual swapper), confirming the intended pattern and the inconsistency in `DepositAllowlistExtension`: [5](#0-4) 

## Impact Explanation
This is an admin-boundary break: the pool admin's deposit allowlist — intended to enforce KYC, regulatory compliance, or LP curation — is bypassed by any unprivileged user via the standard periphery path. A non-allowlisted user can deposit into a curated pool by specifying any allowlisted address as `owner`. The allowlisted user receives LP shares and can subsequently withdraw the deposited assets. No admin cooperation is required; the bypass is immediate and repeatable.

## Likelihood Explanation
High. `MetricOmmPoolLiquidityAdder` is the standard periphery deposit path and explicitly supports `owner != msg.sender`. Allowlisted addresses are observable on-chain via `allowedDepositor` mapping events. Any user with token approvals to the adder can execute the bypass immediately against any pool using `DepositAllowlistExtension`.

The test suite confirms the `owner != msg.sender` path is reachable and functional: [6](#0-5) 

## Recommendation
Check `sender` (the actual depositor/caller) instead of `owner` (the LP position recipient), matching the documented intent ("Gates `addLiquidity` by depositor address") and the pattern used in `SwapAllowlistExtension`:

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
  - Pool deployed with DepositAllowlistExtension
  - Pool admin calls setAllowedToDeposit(pool, alice, true)
  - Bob (not allowlisted) holds token0 and token1, approved to MetricOmmPoolLiquidityAdder

Attack:
  vm.prank(bob);
  liquidityAdder.addLiquidityExactShares(
      pool,
      alice,   // owner = allowlisted address
      salt,
      deltas,
      maxAmount0,
      maxAmount1,
      ""
  );
  // Pool calls _beforeAddLiquidity(sender=adder, owner=alice, ...)
  // Extension checks allowedDepositor[pool][alice] == true → passes
  // Bob pays tokens, alice receives LP shares
  // Allowlist bypassed without any admin action

Verification:
  assertGt(pool.positionBinShares(alice, salt, binIdx), 0); // alice has shares
  // Bob's token balances reduced by deposited amounts
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
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

**File:** metric-periphery/test/MetricOmmPoolLiquidityAdder.t.sol (L303-314)
```text
  function test_weighted_canAddOnBehalfOfAnotherOwner() public {
    LiquidityDelta memory w = _deltaAbovePrice(4, 100_000);
    address bob = makeAddr("bob");
    uint256 cap = 50_000;

    (int8 minBin, uint104 minPos, int8 maxBin, uint104 maxPos) = _unconstrainedCursorBounds();
    vm.prank(alice);
    helper.addLiquidityWeighted(address(pool), bob, 5, w, cap, cap, minBin, minPos, maxBin, maxPos, "");

    uint256 bobShares = stateView.positionBinShares(address(pool), bob, 5, int8(4));
    assertGt(bobShares, 0);
  }
```
