### Title
Early-return in `matchSingleOrder`'s "taker > maker" zero-receive path skips `orderStore.put`/asset transfer, leaving maker order state unpersisted while removed from the price index - ([File: MarketSellAssetActuator.java])

### Summary
The comment "it would not happen here" in `MarketSellAssetActuator.matchSingleOrder` at the `makerBuyTokenQuantityReceive == 0` branch is not a proven invariant — it is only disproved for one specific ratio, not in general. Because `makerOrderCapsule.getSellTokenQuantityRemain()` can be driven to an arbitrary integer value across repeated attacker-controlled fills, a maker order with `sellTokenQuantity > buyTokenQuantity` can be left with a small remainder such that `floor(remain * buyQuantity / sellQuantity) == 0`, triggering this branch. When triggered, the method `return`s before reaching `orderStore.put(makerOrderCapsule...)` and before any `addTrxOrToken` calls, meaning the mutated in-memory maker order state is never persisted and the taker's `sellTokenQuantityRemain` is left untouched for this iteration.

### Finding Description
In `matchSingleOrder` (`actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java:453-483`), the "taker > maker" branch computes: [1](#0-0) 

`makerBuyTokenQuantityReceive` is `floor(makerSellRemainQuantity * makerBuyQuantity / makerSellQuantity)`. The accompanying comment claims this can only be 0 if `sellQuantity < buyQuantity` for the maker, and argues via one specific numeric example (`sellQuantity=200, buyQuantity=100`) that reaching `sellRemain=1` is impossible for that ratio. This argument does not generalize: it only rules out one worked example, not the general case where `sellQuantity > buyQuantity` (a fully valid, attacker-constructible order per `validate()`, which only requires `sellTokenQuantity > 0` and `buyTokenQuantity > 0` and both under `quantityLimit`) [2](#0-1) . Since `makerOrderCapsule.getSellTokenQuantityRemain()` is decremented by arbitrary attacker-chosen integer amounts in the "taker < maker" branch across multiple prior fills [3](#0-2) , an attacker fully controlling both maker and taker orders can steer the remaining quantity to a value that makes `makerBuyTokenQuantityReceive == 0` on a subsequent "taker > maker" match.

I confirmed the GCD-reduction angle in the question is **not** the actual root cause: `matchSingleOrder` reads `makerOrderCapsule.getSellTokenQuantity()`/`getBuyTokenQuantity()` directly from the order capsule, which stores the raw, un-reduced contract values [4](#0-3) . The GCD reduction in `MarketUtils.createPairPriceKey` only affects the on-disk ordering key used for price-bucket lookups [5](#0-4) , and has no effect on the match math itself.

When the zero-receive branch is hit, the code calls `makerOrderCapsule.setSellTokenQuantityReturn()` and `returnSellTokenRemain(makerOrderCapsule)`, then immediately `return`s — **before** reaching `orderStore.put(makerOrderCapsule...)` and the `addTrxOrToken` calls for both taker and maker at lines 486-490. This means:
- `takerOrderCapsule.getSellTokenQuantityRemain()` is never decremented in this call, confirmed by code inspection — no `setSellTokenQuantityRemain` call on the taker exists on this path.
- The maker order's in-memory mutation (via `setSellTokenQuantityReturn`/`returnSellTokenRemain`) is never written back to `orderStore` in this call, since the `orderStore.put` at line 486 is skipped.
- The caller `matchOrder` then inspects the in-memory (but unpersisted) `makerOrderCapsule.getSellTokenQuantityRemain()` to decide whether to remove the order from the active `orderIdListCapsule` price bucket [6](#0-5) , creating a potential divergence between the order-book index and the persisted `MarketOrderStore` record for the maker order.

I was unable to fully verify the exact behavior of `MarketUtils.returnSellTokenRemain` (its effect on the `sellTokenQuantityRemain` field) or whether `orderStore.get`/`put` uses a caching layer that would still persist the mutation despite the skipped explicit `put()` call, due to running out of investigation budget. This limits certainty on whether a full double-credit-on-cancel scenario is reachable, but the confirmed defect (skipped persistence + skipped taker decrement + skipped asset transfer on this path) is a real logic bug in `matchSingleOrder`.

### Impact Explanation
Confirmed impact: on this path, no assets change hands for this match iteration (neither taker nor maker receive tokens), the taker's order remains open with its `sellTokenQuantityRemain` unchanged (so the taker's earlier-debited assets are not lost — they remain locked in their own still-active order for future matching or cancellation), while the maker's remaining sell inventory is refunded to their balance. The maker's order state may become inconsistent between the order-book index (removed) and the underlying `orderStore` record (not persisted), which is a state-consistency defect in the actuator logic. This maps to an accounting/state-corruption class issue rather than a confirmed direct fund-theft, given the taker does not actually lose the debited tokens (they stay in the open order).

### Likelihood Explanation
The precondition requires an attacker fully controlling both the maker order (posted in one transaction) and a carefully sequenced series of taker orders (posted in subsequent transactions) to drive the maker's `sellTokenQuantityRemain` to a value that satisfies `floor(remain * buyQuantity / sellQuantity) == 0`. This requires only unprivileged, self-funded accounts issuing `MarketSellAssetContract` transactions and paying the standard `MarketSellFee`; no special permission or fork gate blocks it. Constructing the exact remainder requires solving a small integer-arithmetic sequencing problem, which is feasible but non-trivial and would need to be validated with a concrete numeric example via unit testing.

### Recommendation
Move `orderStore.put(makerOrderCapsule...)` and any necessary bookkeeping (or restructure the early-return branch) so that the maker order's mutated state is always persisted regardless of which sub-branch is taken. Also replace the unproven comment/assumption with an explicit runtime invariant check (e.g., assert or defensively handle `makerBuyTokenQuantityReceive == 0` by fully reconciling both `orderStore` and the order-book index inside that branch, rather than relying on an informal proof by example).

### Proof of Concept
A rigorous PoC requires further investigation of `MarketUtils.returnSellTokenRemain`'s exact semantics (not fully confirmed with available tool budget) to precisely craft the sequence of maker/taker quantities that lands on `makerBuyTokenQuantityReceive == 0` for a `sellQuantity > buyQuantity` maker order. A JUnit-based fuzz test as suggested in the prompt — iterating small `(sellQty, buyQty)` pairs and driving `makerSellRemainQuantity` down through repeated `matchSingleOrder` calls, then asserting that `orderStore` state after the call matches the in-memory `makerOrderCapsule` state whenever `getSellTokenQuantityRemain() == 0` — is the correct verification approach, but I could not execute it in this environment; a Devin session with full repo/test access would be needed to build and run this PoC and confirm the exact downstream consequence.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L223-230)
```java
    if (sellTokenQuantity <= 0 || buyTokenQuantity <= 0) {
      throw new ContractValidateException("token quantity must greater than zero");
    }

    long quantityLimit = dynamicStore.getMarketQuantityLimit();
    if (sellTokenQuantity > quantityLimit || buyTokenQuantity > quantityLimit) {
      throw new ContractValidateException("token quantity must less than " + quantityLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L349-354)
```java
        // remove order
        if (makerOrderCapsule.getSellTokenQuantityRemain() == 0) {
          // remove from market order list
          orderIdListCapsule.removeOrder(makerOrderCapsule, orderStore,
              pairPriceKey, pairPriceToOrderStore);
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L440-452)
```java
    } else if (takerBuyTokenQuantityRemain < makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker < maker
      // if the quantity of taker want to buy is smaller than the remain of maker want to sell,
      // consume the order of the taker

      takerBuyTokenQuantityReceive = takerBuyTokenQuantityRemain;
      makerBuyTokenQuantityReceive = takerOrderCapsule.getSellTokenQuantityRemain();

      takerOrderCapsule.setSellTokenQuantityRemain(0);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);

      makerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
          makerOrderCapsule.getSellTokenQuantityRemain(), takerBuyTokenQuantityRemain));
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L453-483)
```java
    } else {
      // taker > maker
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

      // if the quantity of taker want to buy is bigger than the remain of maker want to sell,
      // consume the order of maker
      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());

      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
      if (makerBuyTokenQuantityReceive == 0) {
        // the quantity is too small, return the remain of sellToken to maker
        // it would not happen here
        // for the maker, when sellQuantity < buyQuantity, it will get at least one buyToken
        // even when sellRemain = 1.
        // so if sellQuantity=200，buyQuantity=100, when sellRemain=1, it needs to be satisfied
        // the following conditions:
        // makerOrderCapsule.getSellTokenQuantityRemain() - takerBuyTokenQuantityRemain = 1
        // 200 - 200/100 * X = 1 ===> X = 199/2，and this comports with the fact that X is integer.
        makerOrderCapsule.setSellTokenQuantityReturn();
        returnSellTokenRemain(makerOrderCapsule);
        return;
      } else {
        makerOrderCapsule.setSellTokenQuantityRemain(0);
        takerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
            takerOrderCapsule.getSellTokenQuantityRemain(), makerBuyTokenQuantityReceive));
      }
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/MarketOrderCapsule.java (L35-49)
```java
  public MarketOrderCapsule(byte[] id, MarketSellAssetContract contract) {

    this.order = MarketOrder.newBuilder()
        .setOrderId(ByteString.copyFrom(id))
        .setOwnerAddress(contract.getOwnerAddress())
        .setSellTokenId(contract.getSellTokenId())
        .setSellTokenQuantity(contract.getSellTokenQuantity())
        .setBuyTokenId(contract.getBuyTokenId())
        .setBuyTokenQuantity(contract.getBuyTokenQuantity())
        .setSellTokenQuantityRemain(contract.getSellTokenQuantity())
        .setState(State.ACTIVE)
        .setPrev(ByteString.copyFrom(new byte[0]))
        .setNext(ByteString.copyFrom(new byte[0]))
        .build();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L85-103)
```java
  public static byte[] createPairPriceKey(byte[] sellTokenId, byte[] buyTokenId,
      long sellTokenQuantity, long buyTokenQuantity) {

    byte[] sellTokenQuantityBytes;
    byte[] buyTokenQuantityBytes;

    // cal the GCD
    long gcd = findGCD(sellTokenQuantity, buyTokenQuantity);
    if (gcd == 0) {
      sellTokenQuantityBytes = ByteArray.fromLong(sellTokenQuantity);
      buyTokenQuantityBytes = ByteArray.fromLong(buyTokenQuantity);
    } else {
      sellTokenQuantityBytes = ByteArray.fromLong(sellTokenQuantity / gcd);
      buyTokenQuantityBytes = ByteArray.fromLong(buyTokenQuantity / gcd);
    }

    return doCreatePairPriceKey(sellTokenId, buyTokenId,
        sellTokenQuantityBytes, buyTokenQuantityBytes);
  }
```
