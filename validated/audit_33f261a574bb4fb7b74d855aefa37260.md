## Title
Self-matching orders in `MarketSellAssetActuator` cause account balance/asset accounting to be silently overwritten and lost - ([File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java])

## Summary
`MarketSellAssetActuator` never checks whether the taker's own new order and the maker order(s) it matches against belong to the same account address. When a user's order matches against their own resting order (self-trade), the actuator maintains two independent, separately-fetched `AccountCapsule` copies of what is actually the same underlying account — one held in memory throughout `execute()` (the taker's capsule) and one freshly re-read from `AccountStore` inside `addTrxOrToken(MarketOrderCapsule, long)` for the maker side. Because both copies are written back to the store, and the taker's stale in-memory copy is written last, the maker-side credit is clobbered — exactly the "temporary variable overwritten by same-address write" pattern described in the referenced report for `VaultTracker.transferNotionalFrom`.

## Finding Description
In `execute()`, the taker's account is loaded once into a local variable and passed by reference through the whole matching flow: [1](#0-0) 

Balance/asset changes for the taker side are applied only to this in-memory object and persisted a single time at the very end: [2](#0-1) 

However, for the maker side of each match, `addTrxOrToken(MarketOrderCapsule, long)` re-reads the maker's account **fresh from `accountStore`** (a brand-new `AccountCapsule` deserialized from the DB, unrelated to the taker's in-memory object) and immediately persists it: [3](#0-2) 

If the maker order being matched belongs to the *same address* as the taker (i.e., the user's new sell order matches one of their own still-open orders), then:
1. `addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive)` reads the account from the store (still reflecting the balance *before* the current transaction's taker-side deduction), credits `makerBuyTokenQuantityReceive`, and writes it back to the store immediately.
2. At the end of `execute()`, `accountStore.put(accountCapsule.createDbKey(), accountCapsule)` writes the taker's in-memory capsule (which never saw the maker-side credit from step 1) back to the *same key*, overwriting/erasing the maker-side credit that step 1 just persisted.

No validation exists anywhere in `validate()` to reject self-matching orders (only `sellTokenID != buyTokenID` is checked): [4](#0-3) 

This is structurally identical to the reported `VaultTracker.transferNotionalFrom` bug: two temporary copies of state for what is really the same account, where a later unconditional write clobbers an earlier one because "from == to" (here, "maker address == taker address") was never checked.

## Impact Explanation
Any address can trigger this by:
1. Placing a resting sell order (e.g., sell asset A for asset B).
2. Submitting a second `MarketSellAssetContract` from the same address that matches (fully or partially) against their own resting order.

Each self-match causes the credit intended for the "maker" role of that same account to be silently discarded when the final taker-side write flushes the stale in-memory capsule. Because token/TRX accounting entries can be lost this way, this is an accounting-corruption / fund-loss bug reachable directly via a broadcast transaction on the public market actuator — no privileged role is required. Depending on the exact sequencing of multiple matches in a single call (loop in `matchOrder`), the loss can also manifest as asset/balance being deducted without corresponding credit being retained, corrupting account state that is part of consensus (all full nodes replay the same actuator logic, so the corruption is deterministic and consensus-consistent, but it lets a user under specific conditions lose their own expected proceeds or an attacker could probe for cases where the anomaly benefits them due to write ordering).

## Likelihood Explanation
The order of the maker order list depends on price/time priority in `pairPriceToOrderStore`; a user only needs to place two orders such that their own second order matches their own earlier resting order at the same price/pair — this is fully controllable by the caller (choose price so that it matches your own book entry), so the likelihood of triggering this path is high once discovered. It requires no special permissions, just standard `MarketSellAssetContract` broadcasts.

## Recommendation
Reject self-matches, and/or make the maker/taker account handling consistent (i.e., always operate on a single fetched-and-cached `AccountCapsule` per address for the duration of `execute()`, flushed once at the end) so that repeated fetch/write of the same address within one execution can never overwrite pending in-memory changes. As a minimal fix analogous to the report's recommendation: when iterating maker orders in `matchOrder`/`matchSingleOrder`, check if `Arrays.equals(makerOrderCapsule.getOwnerAddress().toByteArray(), takerAccountCapsule's address)` and either skip matching against your own order or route the update through the same in-memory `takerAccountCapsule` object instead of a freshly-fetched copy.

## Proof of Concept
1. Address `A` submits `MarketSellAssetContract` selling `100` of `TOKEN1` for `200` of `TOKEN2`. This creates a resting order (see `createAndSaveOrder`).
2. Address `A` submits a second `MarketSellAssetContract` selling `200` of `TOKEN2` for `100` of `TOKEN1` at a matching price, so that `matchOrder`/`matchSingleOrder` matches this new taker order against `A`'s own resting maker order from step 1.
3. In `matchSingleOrder`, `addTrxOrToken(takerOrderCapsule, ..., takerAccountCapsule)` credits the taker's in-memory capsule for `A`; `addTrxOrToken(makerOrderCapsule, ...)` re-fetches `A`'s account from `accountStore`, credits the maker-side amount, and persists it immediately.
4. At the end of `MarketSellAssetActuator.execute()`, `accountStore.put(accountCapsule.createDbKey(), accountCapsule)` overwrites `A`'s account with the stale taker-side in-memory capsule, discarding the maker-side credit written in step 3.
5. Inspecting `A`'s final `AccountCapsule` in `AccountStore` shows the maker-side proceeds are missing (or, depending on which values get overwritten in which direction, inconsistent from expected double-entry accounting for the self-trade), confirming the account-state corruption caused by the unguarded same-address maker/taker match.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L114-140)
```java
      AccountCapsule accountCapsule = accountStore
          .get(contract.getOwnerAddress().toByteArray());

      sellTokenID = contract.getSellTokenId().toByteArray();
      buyTokenID = contract.getBuyTokenId().toByteArray();
      sellTokenQuantity = contract.getSellTokenQuantity();
      buyTokenQuantity = contract.getBuyTokenQuantity();
      MarketPrice takerPrice = MarketPrice.newBuilder()
          .setSellTokenQuantity(sellTokenQuantity)
          .setBuyTokenQuantity(buyTokenQuantity).build();

      // fee
      accountCapsule.setBalance(accountCapsule.getBalance() - fee);
      // add to blackhole address
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
      // 1. transfer of balance
      transferBalanceOrToken(accountCapsule);

      // 2. create and save order
      MarketOrderCapsule orderCapsule = createAndSaveOrder(accountCapsule, contract);

      // 3. match order
      matchOrder(orderCapsule, takerPrice, ret, accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L146-148)
```java

      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L219-225)
```java
    if (Arrays.equals(sellTokenID, buyTokenID)) {
      throw new ContractValidateException("cannot exchange same tokens");
    }

    if (sellTokenQuantity <= 0 || buyTokenQuantity <= 0) {
      throw new ContractValidateException("token quantity must greater than zero");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L550-562)
```java
  private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num) {
    AccountCapsule accountCapsule = accountStore
        .get(orderCapsule.getOwnerAddress().toByteArray());

    byte[] buyTokenId = orderCapsule.getBuyTokenId();
    if (Arrays.equals(buyTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
    } else {
      accountCapsule
          .addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
    }
    accountStore.put(orderCapsule.getOwnerAddress().toByteArray(), accountCapsule);
  }
```
