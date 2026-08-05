## Title
Griefing DOS via front-run dust participation drains crowdsale supply and reverts legitimate `ParticipateAssetIssueContract` transactions - (`actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java`)

### Summary
`ParticipateAssetIssueActuator` is the java-tron analog of the reported "strict capacity, no-refund" deposit pattern. It converts TRX to a fixed-supply asset and requires the issuer's (`toAccount`) remaining asset balance to be sufficient for the exact computed `exchangeAmount`; if not, the whole transaction reverts with no partial fill and no refund, exactly like the reported `LidoVault.deposit()` design.

### Finding Description
In `validate()`, the actuator computes `exchangeAmount` from the caller's TRX `amount` and checks it against the issuer's remaining token balance: [1](#0-0) 

If `toAccount`'s remaining asset amount is less than the requested `exchangeAmount`, the transaction fails with `"Asset balance is not enough !"` — there is no mechanism to partially fill the order or cap the request to the remaining supply. Since `ParticipateAssetIssueContract` is fully permissionless (any account can call it during the asset's active window), an attacker can observe a pending large participation transaction in the mempool and front-run it with a cheap, small "dust" participation. This reduces the issuer's remaining asset balance just enough that the victim's larger, already-broadcast transaction now fails the `assetBalanceEnoughV2` check in `execute()`: [2](#0-1) 

This mirrors the reported root cause: a strict, all-or-nothing capacity check on a permissionless, value-accepting function with no refund/partial-fill fallback, enabling a cheap dust transaction to repeatedly force reverts of otherwise-valid larger transactions.

### Impact Explanation
Impact is limited to griefing: victims waste transaction fees and are denied participation in that block/attempt, and the attacker can repeat this cheaply to persistently deny large participants from buying into a capped-supply asset issuance. It does not corrupt accounting state or allow theft — it is bypassable by the victim resubmitting with a smaller amount, similar to the "bypass via private relayer" caveat in the original report.

### Likelihood Explanation
Likelihood is medium: `ParticipateAssetIssueContract` is unprivileged and callable by anyone during the active asset window, mempool visibility for front-running is standard, and the attacker's cost is proportional only to the small dust amount needed to tip the remaining supply below the victim's request — not the full remaining supply. There is no direct financial incentive for the attacker beyond griefing, matching the original report's likelihood rationale.

### Recommendation
Modify `ParticipateAssetIssueActuator` to cap/clamp the participation to the remaining available asset balance (partial fill with a corresponding TRX refund for the unfilled portion) instead of reverting the entire transaction when `exchangeAmount` exceeds the issuer's remaining balance, mirroring the recommended fix in the original report (refund excess rather than revert).

### Proof of Concept
1. Issuer creates an asset with a small remaining crowdsale supply near the end of a purchase window.
2. Victim broadcasts `ParticipateAssetIssueContract` requesting `amount` of TRX that would consume close to the entire remaining supply.
3. Attacker observes the pending tx and submits a smaller `ParticipateAssetIssueContract` with negligible `amount`, gets it included first, reducing `toAccount`'s remaining asset balance.
4. Victim's transaction now fails `assetBalanceEnoughV2` in `execute()` (`actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java:85`), reverting with `"reduceAssetAmount failed !"`/validate-time `"Asset balance is not enough !"`, and the victim's fee/gas is wasted while the attacker's dust purchase costs little.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L60-87)
```java
      final ParticipateAssetIssueContract participateAssetIssueContract =
          any.unpack(ParticipateAssetIssueContract.class);
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L182-198)
```java
      int trxNum = assetIssueCapsule.getTrxNum();
      int num = assetIssueCapsule.getNum();
      long exchangeAmount = multiplyExact(amount, num);
      exchangeAmount = floorDiv(exchangeAmount, trxNum);
      if (exchangeAmount <= 0) {
        throw new ContractValidateException("Can not process the exchange!");
      }

      AccountCapsule toAccount = accountStore.get(toAddress);
      if (toAccount == null) {
        throw new ContractValidateException("To account does not exist!");
      }

      if (!toAccount.assetBalanceEnoughV2(assetName, exchangeAmount,
          dynamicStore)) {
        throw new ContractValidateException("Asset balance is not enough !");
      }
```
