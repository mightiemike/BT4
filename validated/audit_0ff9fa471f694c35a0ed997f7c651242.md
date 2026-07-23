Audit Report

## Title
Per-Block Price Velocity Cap Bypassed by Same-Block Multi-Swap Reference Reset — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally writes the current mid-price and `block.number` into storage at lines 57–58 **before** performing the velocity check at lines 60–74. Every subsequent swap in the same block therefore checks velocity against the **previous swap's price** (with `blockDiff = 0`), not the block-start anchor. An attacker chaining N swaps in one block can move the pool mid-price by `N × maxChangePerBlockE18`, completely defeating the guard's stated per-block invariant and enabling bad-price execution against LP bins.

## Finding Description

In `beforeSwap`, the ordering is:

```
L54: prevMid   = s.lastMidPriceX64      // snapshot old price
L55: prevBlock = s.lastUpdateBlock      // snapshot old block

L57: s.lastMidPriceX64  = midPrice     // WRITE new price  ← before check
L58: s.lastUpdateBlock  = block.number // WRITE block num  ← before check

L60-74: if (prevMid != 0) { ... velocity check ... }
``` [1](#0-0) 

After swap 1 in block B executes, storage holds `lastMidPriceX64 = P1`, `lastUpdateBlock = B`. Swap 2 in the same block reads `prevBlock = B`, computes `blockDiff = block.number − B = 0`, and is allowed to move price by a full `maxChange` from P1 (not from P0). The guard's NatSpec states it "Caps how fast the provided price can move **between blocks**", but the implementation caps only per-swap movement from the immediately preceding swap's price. [2](#0-1) 

The fix — only advancing `lastMidPriceX64` when `block.number > lastUpdateBlock` — is never applied. There is no conditional guard on lines 57–58. [3](#0-2) 

## Impact Explanation

An attacker executing N swaps in one block moves the pool mid-price by `N × maxChangePerBlockE18` total while the guard never reverts. LP bins priced along the manipulated curve are drained at prices far outside the intended velocity envelope — direct loss of LP principal through bad-price execution, which is the exact impact the guard was deployed to prevent. This meets the "bad-price execution" and "direct loss of user principal" allowed impact criteria.

## Likelihood Explanation

The attack requires only public `pool.swap()` calls in a single block. No privileged role, oracle admin, or special token behavior is needed. On L2 chains (where Metric OMM is deployed), an attacker controls transaction ordering within their own bundle, making same-block multi-swap sequences trivially achievable and repeatable every block.

## Recommendation

Only advance the stored reference price when entering a new block. When `blockDiff == 0`, keep `prevMid` as the block-start anchor and check cumulative movement from that anchor:

```solidity
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
}
// prevMid and prevBlock already hold the block-start anchor;
// perform the velocity check against them as before
```

This ensures the total allowed price movement per block is bounded by `maxChangePerBlockE18 × sqrt(1 + blockDiff)` measured from the block-start price, regardless of how many swaps occur in the block.

## Proof of Concept

```
Setup: pool admin calls setLastMidPrice → lastMidPriceX64 = P0, lastUpdateBlock = B-1

Block B, tx 1 (attacker):
  pool.swap(...)  →  beforeSwap
    prevMid = P0, prevBlock = B-1
    s.lastMidPriceX64 = P1  (P1 = P0 * (1 + maxChange))
    s.lastUpdateBlock = B
    blockDiff = 1, allowedSq = maxChange² * 2  → passes (actual = maxChange²)

Block B, tx 2 (attacker, same block):
  pool.swap(...)  →  beforeSwap
    prevMid = P1, prevBlock = B          ← reference is now P1, not P0
    s.lastMidPriceX64 = P2  (P2 = P1 * (1 + maxChange))
    s.lastUpdateBlock = B
    blockDiff = 0, allowedSq = maxChange² * 1  → passes (actual = maxChange²)

Result after N txs: price = P0 * (1 + maxChange)^N
Total move ≈ N × maxChange (for small maxChange), zero reverts.
```

A Foundry integration test can confirm this by deploying the extension, configuring a pool with `maxChangePerBlockE18`, calling `swap` N times within `vm.roll` to the same block, and asserting that `priceVelocityState[pool].lastMidPriceX64` has moved by `N × maxChange` from the block-start price without any revert. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L9-18)
```text
/// @title PriceVelocityGuardExtension
/// @notice Caps how fast the provided price can move between blocks, per pool.
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
///
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-58)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L63-70)
```text
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
```
