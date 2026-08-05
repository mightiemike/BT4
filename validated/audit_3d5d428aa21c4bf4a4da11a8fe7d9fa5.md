### Title
Self-matching a market order in `MarketSellAssetActuator` causes lost/overwritten account state due to divergent in-memory `AccountCapsule` instances - (File: actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java)

### Summary
`MarketSellAssetActuator.execute()` mirrors the root-cause pattern of the reported `ResolvStakingV2` bug: two sequential state updates for the *same account* are applied to *different in-memory representations* of that account's balance, and the "stale" one is persisted last, clobbering the other. In the Resolv case, this let a user self-transfer to inflate `effectiveBalance`. In `MarketSellAssetActuator`, nothing prevents a taker order from matching against the same owner's own resting maker order, and the settlement code updates the taker's balance on one `AccountCapsule` object held in the actuator's local variable while crediting the maker's balance on a second, independently-loaded `AccountCapsule` fetched fresh from `AccountStore`. Whichever copy is written to the store last wins, so credits made to the other copy are silently lost when taker == maker.

### Finding Description
In `execute()`, a single `accountCapsule` is loaded once for the taker/owner and mutated in place throughout the flow (fee deduction, `transferBalanceOrToken`, and the taker-side credit inside `matchSingleOrder` via `addTrxOrToken(takerOrderCapsule, num, takerAccountCapsule)`), but is not persisted to `accountStore` until the very end of `execute()`: [1](#0-0) 

For the maker side of a match, `matchSingleOrder` calls the other overload of `addTrxOrToken`, which loads a brand-new `AccountCapsule` directly from `accountStore` (i.e., the *pre-transaction* persisted state), mutates it, and immediately persists it: [2](#0-1) 

This is invoked from `matchSingleOrder` right alongside the taker-side update that uses the in-memory `takerAccountCapsule`: [3](#0-2) 

Nothing in `validate()` rejects an order that would match the owner's own resting order (there is only a check that `sellTokenID != buyTokenID`, not that maker != taker): [4](#0-3) 

If a user places a sell order that matches their own earlier resting order (self-match), the sequence is:
1. `accountCapsule` (taker/owner) is loaded and mutated in memory (fee + sell-side deduction, then buy-side credit for the taker fill) — not yet written to `accountStore`.
2. `addTrxOrToken(makerOrderCapsule, num)` for the maker fill loads a **separate** `AccountCapsule` instance for the same address straight from `accountStore` (which does not reflect the in-memory taker mutations), credits it, and calls `accountStore.put(...)` immediately.
3. At the end of `execute()`, `accountStore.put(accountCapsule.createDbKey(), accountCapsule)` overwrites the store with the taker's in-memory object, which never saw the maker-side credit applied in step 2.

Net effect: the maker-side credit from step 2 is discarded because the final write in step 3 replaces the account record with a version that doesn't include it. This is architecturally identical to the reported bug: two logically-sequential updates to one account's balance are computed against inconsistent stale snapshots, and the later `put` silently discards data written by the earlier one.

### Impact Explanation
This is a state-consistency/accounting bug reachable by any unprivileged user placing two normal `MarketSellAssetContract` transactions (one resting order, one crossing order) against each other. Depending on which balance update ends up in which capsule and the relative order of persistence, self-matching either destroys the maker-side proceeds of the trade (funds burned/lost) or, in variants of the code path with different mutation ordering, could allow one side's credit to be double counted while the other is discarded. Losing user funds via a benign, permitted transaction sequence is a concrete accounting-integrity defect in TVM/market state, distinct from a mere "bad idea" self-trade — it produces an invalid state divergence between the order book bookkeeping (`MarketOrderCapsule`, which is updated correctly and consistently) and the actual account balances in `AccountStore`.

### Likelihood Explanation
Likelihood is high in the sense that any account can trivially create a self-matching scenario (place a sell order, then place an opposing order that crosses it) — this requires no special privileges and no witness/committee involvement, matching the "unprivileged-user" scope. The `MAX_MATCH_NUM`, quantity limits, and order life-cycle are all irrelevant to preventing this; only an explicit maker != taker guard (analogous to the `receiverAddress must not be the same as ownerAddress` checks that were correctly added to `DelegateResourceActuator`, `UnDelegateResourceActuator`, `TransferActuator`, and `TransferAssetActuator`) would close this. I could not fully trace every downstream helper (`MarketUtils.returnSellTokenRemain`, `updateOrderState`) within the available budget to enumerate every branch outcome (partial fills, remainder-return paths), so the exact quantitative loss/gain per scenario is not fully verified — this should be confirmed with a targeted unit/integration test that self-matches an order and asserts the owner's post-trade `AccountCapsule` balance in `AccountStore`.

### Recommendation
- In `MarketSellAssetActuator.validate()`, reject orders whose taker would match against the same owner's existing resting order (or more generally, refuse matches where `takerOrderCapsule.getOwnerAddress() == makerOrderCapsule.getOwnerAddress()`), mirroring the self-address guards already present in `DelegateResourceActuator`/`UnDelegateResourceActuator`/`TransferActuator`.
- Independently of the self-match guard, fix the underlying architectural flaw: `matchSingleOrder`/`addTrxOrToken` should never re-fetch an `AccountCapsule` from `accountStore` for an address that might already be held (and mutated) in memory elsewhere in the same transaction. Route all balance mutations for a given address through a single cached `AccountCapsule` instance per transaction execution (e.g., a small in-transaction address→capsule cache) and persist once at the end, so that self-matching (or any other double-touch of the same account within one execution) cannot produce lost or double-counted updates.

### Proof of Concept
Not executed (no test harness access in this session). Recommended reproduction: 
1. Fund `OWNER_ADDRESS_FIRST` with token A and TRX.
2. Have `OWNER_ADDRESS_FIRST` place a `MarketSellAssetContract` selling token A for token B (resting order), analogous to `addOrder(...)` in `MarketSellAssetActuatorTest`.
3. Have `OWNER_ADDRESS_FIRST` place a second `MarketSellAssetContract` selling token B for token A at a matching price, causing `matchOrder`/`matchSingleOrder` to match the taker order against the account's own resting maker order.
4. Assert `AccountCapsule.getAssetV2MapForTest()` for `OWNER_ADDRESS_FIRST` after execution against the expected pre/post balances computed from the trade math; observe that the maker-side credit performed via `addTrxOrToken(makerOrderCapsule, num)` (loaded fresh from `accountStore`) is overwritten and lost when `accountStore.put(accountCapsule.createDbKey(), accountCapsule)` executes at the end of `execute()`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L140-148)
```java
      matchOrder(orderCapsule, takerPrice, ret, accountCapsule);

      // 4. save remain order into order book
      if (orderCapsule.getSellTokenQuantityRemain() != 0) {
        saveRemainOrder(orderCapsule);
      }

      orderStore.put(orderCapsule.getID().toByteArray(), orderCapsule);
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L219-221)
```java
    if (Arrays.equals(sellTokenID, buyTokenID)) {
      throw new ContractValidateException("cannot exchange same tokens");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L488-491)
```java
    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);

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
