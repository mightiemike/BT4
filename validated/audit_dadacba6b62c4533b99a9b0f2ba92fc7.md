Audit Report

## Title
`PriceVelocityGuardExtension` Unconditional Per-Swap Reference-Price Update Allows Cumulative Block-Level Oracle Movement to Exceed the Configured Cap — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally overwrites `lastMidPriceX64` and `lastUpdateBlock` with the current oracle mid-price on every swap before performing the velocity check. When multiple swaps execute in the same block, each hop is compared against the previous swap's price rather than the block-open price. Because `blockDiff = 0` for all intra-block swaps, the allowed movement is `maxChange` per hop, and N swaps in the same block can move the oracle-derived mid by approximately `N × maxChange` — far beyond the intended single-block cap. Both Pyth Lazer and Chainlink Data Streams are permissionless push-based oracles that allow any holder of a valid signed report to update the on-chain price between transactions in the same block, making this attack directly reachable.

## Finding Description

In `PriceVelocityGuardExtension.beforeSwap`, lines 57–58 unconditionally write the new mid-price to storage before the velocity check is performed:

```solidity
s.lastMidPriceX64 = midPrice;
s.lastUpdateBlock = uint64(block.number);
``` [1](#0-0) 

When a second swap arrives in the same block, `prevBlock` equals `block.number` (set by the first swap), so `blockDiff = 0`:

```solidity
uint256 blockDiff = block.number - prevBlock;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
// = maxChange^2 * 1 = maxChange^2
``` [2](#0-1) 

The check compares the current price only against the previous swap's price (already updated), not the block-open price. Each hop of `maxChange` passes individually, but N swaps in the same block can move the oracle-derived mid by approximately `N × maxChange` in total.

The oracle price can genuinely change between swaps in the same block because both `PythOracle.fallback()` and `ChainlinkOracle.updateReport()` are permissionless push paths. For Pyth Lazer, `_verifyAndStore` accepts any update with a strictly newer `FeedUpdateTimestamp` (millisecond precision):

```solidity
if (ts.isAfter(__data[feedId].timestampMs)) {
    __data[feedId] = IOffchainOracle.OracleData({...});
}
``` [3](#0-2) 

For Chainlink Data Streams, `_store` similarly accepts any report with a newer timestamp:

```solidity
if (d.timestampMs.isAfter(oracleData[feedId].timestampMs)) {
    oracleData[feedId] = d;
}
``` [4](#0-3) 

The pool's reentrancy guard uses transient storage that resets between transactions, so multiple swaps per block are explicitly supported: [5](#0-4) 

The pool fetches a fresh oracle quote on every `swap()` call via `_getBidAndAskPriceX64()`, which calls `IPriceProvider.getBidAndAskPrice()` — reading the latest stored oracle value at that moment: [6](#0-5) 

The NatSpec states the extension "Caps how fast the provided price can move **between blocks**" with allowed deviation `maxChangePerBlockE18 * sqrt(1 + blockDifference)`, but the implementation fails to enforce this invariant for multiple swaps within the same block. [7](#0-6) 

## Impact Explanation

The velocity guard is the LP-protection layer against rapid oracle price manipulation. By walking the oracle mid across many small steps within one block — each satisfying the per-hop cap — an attacker can execute swaps at prices increasingly unfavorable to LPs. Because the pool settles every swap at the live oracle price, LPs bear the loss on each leg. The cumulative drain is proportional to the number of hops and the per-hop cap, limited only by available liquidity and oracle update throughput, not by the configured `maxChangePerBlockE18`. This constitutes a direct loss of LP principal, qualifying as a High-severity impact under the allowed impact gate (bad-price execution reaching pool swaps; direct loss of LP assets).

## Likelihood Explanation

Pyth Lazer and Chainlink Data Streams are push-based: any party holding a valid signed price report can submit it on-chain without any privileged role. A block builder or MEV searcher can bundle multiple `(oracle-update, pool.swap)` transaction pairs in a single block. The oracle's per-feed monotonicity check only requires a strictly newer `FeedUpdateTimestamp` (millisecond precision), so multiple distinct updates within the same block's wall-clock second are feasible. No special setup beyond holding valid oracle reports (publicly available from off-chain infrastructure) is required. [8](#0-7) [9](#0-8) 

## Recommendation

Only update `lastMidPriceX64` and `lastUpdateBlock` when `block.number > prevBlock` (i.e., on the first swap of a new block). For all subsequent swaps within the same block, compare against the block-open price and leave `lastMidPriceX64` unchanged:

```solidity
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
}
// velocity check always uses the block-open reference (prevMid)
```

This ensures the guard enforces a true per-block cap regardless of how many swaps occur within the block.

## Proof of Concept

Setup: pool with `PriceVelocityGuardExtension`, `maxChangePerBlockE18 = 0.01e18` (1% per block).

1. Oracle mid starts at `P₀ = 1.000`. `lastMidPriceX64 = P₀`, `lastUpdateBlock = B`.
2. Attacker submits Pyth Lazer report with newer `FeedUpdateTimestamp` → oracle mid = `P₁ = 1.010` (+1%).
3. Attacker calls `pool.swap()` in block `B` — `beforeSwap` reads `prevMid = P₀`, `prevBlock = B`, writes `lastMidPriceX64 = P₁`, checks `|P₁−P₀|/P₀ = 1% ≤ 1%` ✓.
4. Attacker submits next Pyth Lazer report (newer ms timestamp, same block) → oracle mid = `P₂ = 1.0201` (+1% from `P₁`).
5. Attacker calls `pool.swap()` in block `B` — `beforeSwap` reads `prevMid = P₁`, `prevBlock = B`, `blockDiff = 0`, writes `lastMidPriceX64 = P₂`, checks `|P₂−P₁|/P₁ = 1% ≤ 1%` ✓.
6. Repeat 10 times in the same block.

Result: oracle mid moves from 1.000 to `(1.01)^10 ≈ 1.105` (+10.5%) within a single block, while the guard allowed each individual hop. LPs traded against a price that drifted 10× the configured cap.

A Foundry fork test can reproduce this by: deploying a pool with `PriceVelocityGuardExtension`, using a `MockOracle` whose `getBidAndAskPrice()` can be updated between calls, and calling `pool.swap()` multiple times in the same block with incrementally higher oracle prices, asserting that the cumulative movement exceeds `maxChangePerBlockE18` while each individual call succeeds. [10](#0-9)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L10-18)
```text
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-79)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
      }
    }

    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** smart-contracts-poc/contracts/oracles/utils/LazerConsumer.sol (L164-171)
```text
                if (ts.isAfter(__data[feedId].timestampMs)) {
                    __data[feedId] = IOffchainOracle.OracleData({
                        price: normPrice,
                        spread0: spreadU.toUint16(),
                        spread1: 0xFFFF,
                        timestampMs: ts
                    });
                }
```

**File:** smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol (L68-76)
```text
    function updateReport(bytes calldata fullReport) external {
        _store(_verifyReport(fullReport));
    }

    function updateReports(bytes[] calldata fullReports) external {
        for (uint256 i; i < fullReports.length; ++i) {
            _store(_verifyReport(fullReports[i]));
        }
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol (L91-94)
```text
        if (d.timestampMs.isAfter(oracleData[feedId].timestampMs)) {
            oracleData[feedId] = d;
            emit ReportStored(feedId, d.price, d.spread0, d.timestampMs);
        }
```

**File:** metric-core/contracts/utils/MetricReentrancyGuardTransient.sol (L40-42)
```text
  function _nonReentrantAfter() internal {
    TransientSlot.tstore(TransientSlot.asUint256(_reentrancyGuardStorageSlot()), 0);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L804-813)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
      revert PriceProviderFailed(reason);
    }
  }
```

**File:** smart-contracts-poc/contracts/oracles/providers/PythOracle.sol (L39-60)
```text
    fallback() payable external override {
        uint256 end;

        assembly ("memory-safe") {
            end := calldatasize()
        }

        uint256 feedsLength;
        assembly ("memory-safe") {
            feedsLength := shr(240, calldataload(0)) // first 2 bytes
        }

        uint32[] memory updateFeedIds = new uint32[](feedsLength);
        assembly ("memory-safe") {
            let dst := add(updateFeedIds, 32)  // skip length slot
            let src := 2                       // offset after feedsLength(2)

            for { let i := 0 } lt(i, feedsLength) { i := add(i, 1) } {
                // load 32 bytes, shift right to get uint32 from high bits
                mstore(dst, shr(224, calldataload(src)))
                dst := add(dst, 32)
                src := add(src, 4)
```
