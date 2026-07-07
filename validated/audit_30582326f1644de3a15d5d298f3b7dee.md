### Title
`nonDefaultFeeTierMask` Typed as `uint128` Silently Drops Custom Fee Rates for Tiers ≥ 128 — (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`nonDefaultFeeTierMask` is a `uint128` bitmask used to track which fee tiers have custom rates configured. Because `tier` is `uint32`, any tier value ≥ 128 causes a silent shift-overflow to zero, permanently preventing custom fee rates from being applied to those tiers.

---

### Finding Description

In `OffchainExchange.sol`, `updateTierFeeRates` stores custom fee rates and marks the tier as non-default via a bitmask:

```solidity
nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

In Solidity 0.8+, shifting a `uint128` by ≥ 128 positions yields `0`. So for any `txn.tier >= 128`, the expression `uint128(1) << txn.tier` evaluates to `0`, and the mask is never updated. [1](#0-0) 

The read path in `getTierFeeRateX18` then checks:

```solidity
if (nonDefaultFeeTierMask & (1 << tier) != 0) {
    return feeRates[tier][productId];
}
return FeeRates({ makerRateX18: 0, takerRateX18: 200_000_000_000_000 }); // 2 bps
``` [2](#0-1) 

For `tier >= 128`, `nonDefaultFeeTierMask & (1 << tier)` is always `0` (the `uint128` mask has no bits above position 127), so the function unconditionally returns the hardcoded 2 bps default. The rates stored in `feeRates[tier][productId]` are permanently unreachable.

The `tier` field in both `UpdateTierFeeRates` and `UpdateFeeTier` is `uint32`, meaning the protocol's own data model supports tiers up to ~4 billion, but the bitmask can only represent tiers 0–127. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Any user assigned a fee tier ≥ 128 via `updateFeeTier` will always be charged the default 2 bps taker rate, regardless of what custom rates were configured for that tier via `updateTierFeeRates`. Two concrete harms follow:

1. **Protocol revenue loss**: If tier ≥ 128 was intended as a higher-fee tier (e.g., for penalized or high-risk accounts), those users pay only 2 bps instead of the configured higher rate. The protocol permanently under-collects fees.
2. **VIP rate misconfiguration**: If tier ≥ 128 was intended as a discounted tier, users pay 2 bps instead of the lower configured rate — a lesser but still incorrect outcome.

In both cases the `feeRates[tier][productId]` storage slot is written but never read, making the `updateTierFeeRates` call a no-op for any tier ≥ 128. [5](#0-4) [6](#0-5) 

---

### Likelihood Explanation

The `tier` field is `uint32` throughout the interface. There is no on-chain validation preventing the sequencer from assigning tiers ≥ 128. As the protocol scales and adds more fee tiers (e.g., tiered VIP levels, builder-specific tiers, penalized tiers), values ≥ 128 are a natural operational choice. The failure is silent — no revert occurs, the storage write succeeds, and the misconfiguration is only observable by comparing expected vs. actual fee charges. [7](#0-6) 

---

### Recommendation

Replace the `uint128` bitmask with a `mapping(uint32 => bool)` to track which tiers have custom rates, removing the 128-tier ceiling entirely:

```solidity
// Replace:
uint128 internal nonDefaultFeeTierMask;

// With:
mapping(uint32 => bool) internal isNonDefaultFeeTier;
```

Update the write path:
```solidity
isNonDefaultFeeTier[txn.tier] = true;
```

Update the read path:
```solidity
if (isNonDefaultFeeTier[tier]) {
    return feeRates[tier][productId];
}
```

---

### Proof of Concept

1. Sequencer calls `updateTierFeeRates` with `tier = 200`, `takerRateX18 = 5e15` (50 bps).
2. Inside `updateTierFeeRates`: `feeRates[200][productId]` is written correctly, but `nonDefaultFeeTierMask |= uint128(1) << 200` evaluates to `nonDefaultFeeTierMask |= 0` — mask unchanged.
3. Sequencer calls `updateFeeTier` assigning user Alice to tier 200.
4. Alice submits a trade. `getTierFeeRateX18(200, productId)` checks `nonDefaultFeeTierMask & (1 << 200)` = `0`, falls through to the default, and returns 2 bps instead of 50 bps.
5. Protocol collects 2 bps instead of the configured 50 bps on every Alice trade. [8](#0-7)

### Citations

**File:** core/contracts/OffchainExchange.sol (L481-506)
```text
        bool taker
    ) internal view returns (FeeInfo memory) {
        (uint32 builderId, int128 builderFeeRate) = _builderInfo(appendix);
        Builder memory builder;
        if (builderId != 0) {
            builder = builders[builderId];
            if (
                builder.owner == address(0) ||
                builderFeeRate > builder.highestFeeRate ||
                builderFeeRate < builder.lowestFeeRate
            ) {
                revert(ERR_INVALID_BUILDER);
            }
        } else if (builderFeeRate != 0) {
            revert(ERR_INVALID_BUILDER);
        }

        uint32 feeTier = feeTiers[address(uint160(bytes20(sender)))];
        if (feeTier < builder.defaultFeeTier) {
            feeTier = builder.defaultFeeTier;
        }
        FeeRates memory userFeeRates = getTierFeeRateX18(feeTier, productId);
        int128 feeRate = taker
            ? userFeeRates.takerRateX18
            : userFeeRates.makerRateX18;
        return FeeInfo(feeRate, builderId, builderFeeRate);
```

**File:** core/contracts/OffchainExchange.sol (L933-946)
```text
    function getTierFeeRateX18(uint32 tier, uint32 productId)
        public
        view
        returns (FeeRates memory)
    {
        if (nonDefaultFeeTierMask & (1 << tier) != 0) {
            return feeRates[tier][productId];
        }
        return
            FeeRates({
                makerRateX18: 0,
                takerRateX18: 200_000_000_000_000 // 2 bps
            });
    }
```

**File:** core/contracts/OffchainExchange.sol (L952-959)
```text
    function updateFeeTier(address user, uint32 newTier) external {
        require(msg.sender == address(clearinghouse), ERR_UNAUTHORIZED);
        if (newTier != 0 && !addressTouched[user]) {
            addressTouched[user] = true;
            customFeeAddresses.push(user);
        }
        feeTiers[user] = newTier;
        emit FeeTierUpdate(user, newTier);
```

**File:** core/contracts/OffchainExchange.sol (L962-990)
```text
    function updateTierFeeRates(IEndpoint.UpdateTierFeeRates memory txn)
        external
        onlyEndpoint
    {
        if (txn.productId == QUOTE_PRODUCT_ID) {
            uint32[] memory spotProductIds = spotEngine.getProductIds();
            uint32[] memory perpProductIds = perpEngine.getProductIds();
            for (uint32 i = 0; i < spotProductIds.length; i++) {
                if (spotProductIds[i] == QUOTE_PRODUCT_ID) {
                    continue;
                }
                feeRates[txn.tier][spotProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
            for (uint32 i = 0; i < perpProductIds.length; i++) {
                feeRates[txn.tier][perpProductIds[i]] = FeeRates(
                    txn.makerRateX18,
                    txn.takerRateX18
                );
            }
        } else {
            feeRates[txn.tier][txn.productId] = FeeRates(
                txn.makerRateX18,
                txn.takerRateX18
            );
        }
        nonDefaultFeeTierMask |= uint128(1) << txn.tier;
```

**File:** core/contracts/interfaces/IEndpoint.sol (L233-236)
```text
    struct UpdateFeeTier {
        address user;
        uint32 newTier;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L238-243)
```text
    struct UpdateTierFeeRates {
        uint32 tier;
        uint32 productId;
        int128 makerRateX18;
        int128 takerRateX18;
    }
```
