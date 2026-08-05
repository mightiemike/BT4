### Title
Rounding loss in `ParticipateAssetIssueActuator` causes users to overpay TRX relative to TRC10 tokens received - (File: `actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java`)

### Summary
`ParticipateAssetIssueActuator.execute()` deducts the user's full TRX payment (`cost`) but credits the user with only `floor(cost * num / trxNum)` TRC10 tokens, exactly mirroring the AaveV3 `supplyTokenTo()` rounding bug: the amount taken from the payer is not adjusted to match the rounded-down amount actually delivered, so the truncated fraction is lost by the buyer and effectively captured by the asset issuer.

### Finding Description
In `execute()`, the actuator subtracts the entire `cost` (the TRX amount specified by the caller) from the owner's balance and credits the full `cost` to the issuer's balance, while computing the TRC10 token amount to transfer with integer floor division: [1](#0-0) 

```
long cost = participateAssetIssueContract.getAmount();
...
long exchangeAmount = multiplyExact(cost, assetIssueCapsule.getNum());
exchangeAmount = floorDiv(exchangeAmount, assetIssueCapsule.getTrxNum());
ownerAccount.addAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore);
...
toAccount.setBalance(addExact(toAccount.getBalance(), cost));
```

The same floor-division pattern is repeated in `validate()`: [2](#0-1) 

This is structurally identical to the reported Aave issue: the contract computes a rounded-down output (`_shares` / `exchangeAmount`) from a user-supplied input (`_depositAmount` / `cost`), but transfers the *full, un-rounded* input amount instead of recomputing the input to match the rounded output (i.e. `_depositAmount = _sharesToToken(_shares)`). Because `num`/`trxNum` are attacker-controllable at asset-issuance time (`AssetIssueContract`), an issuer can set a ratio where `trxNum` is large relative to `num`, maximizing the truncation on every `ParticipateAssetIssueContract` call, so buyers systematically receive less TRC10 value than the TRX they paid, with the difference retained by the issuer's account (`toAccount.setBalance(addExact(...,cost))`) instead of being reduced or refunded.

### Impact Explanation
Every participation in a TRC10 asset issue with an unfavorable `num`/`trxNum` ratio (low precision, i.e. `trxNum` large relative to `num`) causes the buyer to lose the truncated remainder on each call. Because the actuator has no fee (`calcFee()` returns 0) and no minimum precision requirement is imposed on `num`/`trxNum` at issuance, an issuer can amplify this loss by choosing extreme ratios, and buyers can be induced to lose value across many small `amount` calls (loss accumulates per-call, not per-total). This is a genuine, unprivileged, on-chain fund-accounting issue affecting ordinary token buyers, matching the report's "user fund loss via rounding" class.

### Likelihood Explanation
`ParticipateAssetIssueContract` is a standard user-facing transaction type that any account can send against any active TRC10 asset issue; no privileged role is required. The rounding always triggers whenever `cost * num` is not exactly divisible by `trxNum`, which is the common case for arbitrary `amount` values, making this reliably and repeatedly exploitable/observable rather than a theoretical edge case.

### Recommendation
Mirror the Code4rena-recommended fix: after computing `exchangeAmount` via floor division, recompute the actual TRX `cost` charged to the buyer (and credited to the issuer) as the exact TRX equivalent of `exchangeAmount` (i.e. `cost = exchangeAmount * trxNum / num`, rounded consistently), rather than using the caller-supplied `cost` verbatim. Alternatively, refund the truncated remainder back to the buyer so the amount debited always matches the value of tokens actually received.

### Proof of Concept
1. An asset issuer creates a TRC10 asset with `num = 1`, `trxNum = 1000` (low precision ratio), via `AssetIssueContract`.
2. A buyer calls `ParticipateAssetIssueContract` with `amount (cost) = 1999`.
3. In `execute()`: `exchangeAmount = floorDiv(1999 * 1, 1000) = 1`.
4. The buyer's TRX balance is debited the full `1999` (`ownerAccount.setBalance(balance - cost - fee)`), and the issuer's balance is credited the full `1999` TRX (`toAccount.setBalance(addExact(toAccount.getBalance(), cost))`), while the buyer only receives `1` token instead of the fair value of `1.999` tokens.
5. The buyer has effectively paid for ~2 tokens but received only 1, losing roughly half the paid value to rounding, with the loss fully captured by the issuer — reproducing the same value-loss pattern as the referenced `supplyTokenTo()` bug.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L62-91)
```java
      long cost = participateAssetIssueContract.getAmount();

      //subtract from owner address
      byte[] ownerAddress = participateAssetIssueContract.getOwnerAddress().toByteArray();
      AccountCapsule ownerAccount = accountStore.get(ownerAddress);
      long balance = subtractExact(ownerAccount.getBalance(), cost);
      balance = subtractExact(balance, fee);
      ownerAccount.setBalance(balance);
      byte[] key = participateAssetIssueContract.getAssetName().toByteArray();

      //calculate the exchange amount
      AssetIssueCapsule assetIssueCapsule;
      assetIssueCapsule = Commons
          .getAssetIssueStoreFinal(dynamicStore, assetIssueStore, assetIssueV2Store).get(key);

      long exchangeAmount = multiplyExact(cost, assetIssueCapsule.getNum());
      exchangeAmount = floorDiv(exchangeAmount, assetIssueCapsule.getTrxNum());
      ownerAccount.addAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore);

      //add to to_address
      byte[] toAddress = participateAssetIssueContract.getToAddress().toByteArray();
      AccountCapsule toAccount = accountStore.get(toAddress);
      toAccount.setBalance(addExact(toAccount.getBalance(), cost));
      if (!toAccount.reduceAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }

      //write to db
      accountStore.put(ownerAddress, ownerAccount);
      accountStore.put(toAddress, toAccount);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L182-188)
```java
      int trxNum = assetIssueCapsule.getTrxNum();
      int num = assetIssueCapsule.getNum();
      long exchangeAmount = multiplyExact(amount, num);
      exchangeAmount = floorDiv(exchangeAmount, trxNum);
      if (exchangeAmount <= 0) {
        throw new ContractValidateException("Can not process the exchange!");
      }
```
