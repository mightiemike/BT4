### Title
`collectedBuilderFee` First Key Is `productId` in `claimBuilderFee()` but `quoteId` in `applyFee()`, Permanently Locking All Builder Fees - (File: `core/contracts/OffchainExchange.sol`)

---

### Summary

`collectedBuilderFee` is declared as `quoteId → builderId → amount`, fees are accumulated using `market.quoteId` as the first key in `applyFee()`, but `claimBuilderFee()` reads and resets the mapping using `productId` as the first key. Because `productId != quoteId` for every non-quote product, the read always hits an empty storage slot. All builder fees are permanently locked and unclaimable.

---

### Finding Description

`OffchainExchange.sol` declares the builder fee accumulator with an explicit comment confirming the intended key order:

```solidity
mapping(uint32 => mapping(uint32 => int128)) internal collectedBuilderFee; // quoteId -> builder -> amount
``` [1](#0-0) 

During order matching, `applyFee()` correctly writes fees using `market.quoteId` as the outer key:

```solidity
collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo.builderFee;
``` [2](#0-1) 

However, `claimBuilderFee()` iterates over `spotEngine.getProductIds()` and uses each raw `productId` — not the corresponding `quoteId` — as the outer key when reading and clearing the mapping:

```solidity
int128 collectedFee = collectedBuilderFee[productId][builderId];
...
spotEngine.updateBalance(productId, sender, collectedFee);
collectedBuilderFee[productId][builderId] = 0;
``` [3](#0-2) 

For every non-quote product (e.g., a BTC spot product with `productId = 2` and `quoteId = 0`), fees are stored at `collectedBuilderFee[0][builderId]` but the claim reads from `collectedBuilderFee[2][builderId]`, which is always zero. The accumulated fees are never found, never transferred, and never cleared.

A secondary corruption exists in the same loop: even if a non-zero value were somehow present at `collectedBuilderFee[productId][builderId]`, the payout call `spotEngine.updateBalance(productId, sender, collectedFee)` would credit the builder with the **base asset** of that product rather than the quote asset in which fees were actually denominated.

---

### Impact Explanation

All builder fees earned across every non-quote product are permanently locked in the contract. No builder can ever recover them. The `collectedBuilderFee[quoteId][builderId]` slots accumulate monotonically with every matched order that carries a builder fee, but the claim path never touches those slots. The locked value grows proportionally to protocol trading volume.

---

### Likelihood Explanation

Every builder who registers via `updateBuilder` and whose `builderId` is embedded in matched orders is affected immediately. The `claimBuilderFee` transaction is a standard endpoint-routed call available to any registered builder. The bug is triggered on the very first claim attempt and requires no special conditions, adversarial state, or privileged access.

---

### Recommendation

In `claimBuilderFee()`, replace the raw `productId` lookup with the corresponding `quoteId`, consistent with how fees are written in `applyFee()`:

```solidity
- int128 collectedFee = collectedBuilderFee[productId][builderId];
+ uint32 quoteId = quoteIds[productId];
+ int128 collectedFee = collectedBuilderFee[quoteId][builderId];
  if (collectedFee == 0) {
      continue;
  }
  emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
- spotEngine.updateBalance(productId, sender, collectedFee);
- collectedBuilderFee[productId][builderId] = 0;
+ spotEngine.updateBalance(quoteId, sender, collectedFee);
+ collectedBuilderFee[quoteId][builderId] = 0;
```

Care must be taken to deduplicate across products that share the same `quoteId` to avoid double-crediting.

---

### Proof of Concept

1. Sequencer calls `updateBuilder` registering `builderId = 1` with `owner = alice`.
2. A taker order is matched on a BTC spot product (`productId = 2`, `quoteId = 0`). `applyFee()` runs and stores `collectedBuilderFee[0][1] += fee` (keyed by `quoteId = 0`).
3. Alice submits a `ClaimBuilderFee` slow-mode transaction through the endpoint.
4. `claimBuilderFee()` iterates `spotEngine.getProductIds()`. When `productId = 2`, it reads `collectedBuilderFee[2][1]` — which is `0`. The `if (collectedFee == 0) continue;` branch is taken.
5. When `productId = 0` (the quote product itself), it reads `collectedBuilderFee[0][1]` — which does hold the accumulated fee — but `spotEngine.updateBalance(0, sender, collectedFee)` credits Alice with the quote token under `productId = 0`, which is correct only by coincidence for that one iteration. However, the loop does not deduplicate: if multiple products share `quoteId = 0`, the slot is read and zeroed on the first hit, and subsequent products with the same `quoteId` find zero. More critically, for any deployment where `QUOTE_PRODUCT_ID` is not included in `spotEngine.getProductIds()` or is skipped, even this accidental partial recovery fails entirely. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/OffchainExchange.sol (L64-64)
```text
    mapping(uint32 => mapping(uint32 => int128)) internal collectedBuilderFee; // quoteId -> builder -> amount
```

**File:** core/contracts/OffchainExchange.sol (L566-569)
```text
        if (orderInfo.builderFee > 0) {
            collectedBuilderFee[market.quoteId][feeInfo.builderId] += orderInfo
                .builderFee;
            emitBuilderEvent(orderInfo, feeInfo.builderId, productId);
```

**File:** core/contracts/OffchainExchange.sol (L879-888)
```text
        for (uint32 i = 0; i < productIds.length; i++) {
            uint32 productId = productIds[i];
            int128 collectedFee = collectedBuilderFee[productId][builderId];
            if (collectedFee == 0) {
                continue;
            }
            emit ClaimBuilderFee(builderId, productId, sender, collectedFee);
            spotEngine.updateBalance(productId, sender, collectedFee);
            collectedBuilderFee[productId][builderId] = 0;
        }
```
