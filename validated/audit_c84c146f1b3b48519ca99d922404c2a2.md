Found it. In `ExchangeWithdrawActuator.execute()`, `calcFee()` is invoked and `newBalance = subtractExact(accountCapsule.getBalance(), calcFee())` deducts the fee from the owner's balance, but there is **no corresponding credit anywhere** — the fee is neither burned via `dynamicStore.burnTrx()` nor sent to the blackhole address via `adjustBalance(accountStore, accountStore.getBlackhole(), fee)`, unlike every other fee-charging actuator in the codebase (`TransferActuator`, `AssetIssueActuator`, `ExchangeCreateActuator`, `CreateAccountActuator`, `WitnessCreateActuator`, etc.), which all consistently do `burnTrx(fee)` / credit-to-blackhole after deducting the fee from the sender. [1](#0-0) 

Compare this to the pattern used everywhere else, e.g. `ExchangeCreateActuator`: [2](#0-1) 

and `TransferActuator`: [3](#0-2) 

### Title
Exchange withdraw fee is deducted from the user but never burned or credited to the blackhole/fee-pool - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
`ExchangeWithdrawActuator.execute()` subtracts the exchange-withdraw fee from the caller's TRX balance via `subtractExact(accountCapsule.getBalance(), calcFee())`, but unlike every other fee-charging actuator in java-tron, it never calls `dynamicPropertiesStore.burnTrx(fee)` nor credits the fee to `accountStore.getBlackhole()`. The TRX is simply removed from the user's account and never re-appears in total-supply accounting anywhere.

### Finding Description
Every actuator in java-tron that charges a system fee follows the same two-step pattern: (1) deduct the fee from the owner's account, and (2) either burn it via `dynamicPropertiesStore.burnTrx(fee)` (when `supportBlackHoleOptimization()` is enabled) or credit it to the blackhole address via `adjustBalance(accountStore, accountStore.getBlackhole(), fee)`. This pattern is present in `TransferActuator`, `TransferAssetActuator`, `AssetIssueActuator`, `CreateAccountActuator`, `ExchangeCreateActuator`, `WitnessCreateActuator`, `AccountPermissionUpdateActuator`, `Manager.consumeMultiSignFee`/`consumeMemoFee`, and `ResourceProcessor.consumeFeeForBandwidth`/`consumeFeeForNewAccount`.

`ExchangeWithdrawActuator.execute()` breaks this pattern: it computes `long fee = calcFee();` and uses it only to compute `newBalance = subtractExact(accountCapsule.getBalance(), calcFee())`, which is then used as the base for crediting the withdrawn token amounts back to the user. There is no subsequent `burnTrx(fee)` or blackhole credit anywhere in the method. The fee amount is silently subtracted from the account balance and disappears from the ledger's tracked flows (it is not burned, not pooled, and not credited to any address), exactly analogous to the reported ClaggBaseAdapter issue where the performance fee is deducted from the withdrawal accounting but never actually transferred out to a treasury.

### Impact Explanation
Because `dynamicPropertiesStore.burnTrx()` also updates `BURN_TRX_AMOUNT`, which factors into total-supply/black-hole accounting used elsewhere in the protocol (e.g., for reward/inflation calculations and audits of circulating supply), a silently vanishing fee that is neither burned nor pooled nor sent to the blackhole account creates a discrepancy between the sum of individually-tracked TRX destinations (blackhole balance + `BURN_TRX_AMOUNT` + transaction fee pool) and the actual amount removed from user balances. This is a state/accounting divergence bug: TRX is destroyed from a user's balance without being accounted for anywhere in the system's fee-tracking totals, unlike the identical operations performed by every sibling actuator.

### Likelihood Explanation
This code path executes on every successful `ExchangeWithdrawContract` transaction, which is a normal, unprivileged, user-initiated operation (any account that created a bancor-style exchange pair can call `ExchangeWithdraw`). No special permissions are required, so the divergence occurs deterministically each time this common actuator runs.

### Recommendation
Add the same fee-disposal logic used in `ExchangeCreateActuator`/`ExchangeInjectActuator` right after the fee is deducted from the owner's balance in `ExchangeWithdrawActuator.execute()`:
```java
if (dynamicStore.supportBlackHoleOptimization()) {
  dynamicStore.burnTrx(fee);
} else {
  adjustBalance(accountStore, accountStore.getBlackhole(), fee);
}
```

### Proof of Concept
1. Call `ExchangeCreateContract` to create an exchange pair with a TRX/token liquidity pool (owner becomes `exchangeCapsule.getCreatorAddress()`).
2. As the owner, call `ExchangeWithdrawContract` to withdraw liquidity; `ExchangeWithdrawActuator.execute()` runs `long fee = calcFee();` [4](#0-3)  and `long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());` [5](#0-4) .
3. Observe that `accountCapsule.setBalance(...)` reflects the fee deduction, `ret.setStatus(fee, code.SUCESS)` reports the fee, but neither `dynamicStore.burnTrx(fee)` nor `accountStore.getBlackhole()` receives any credit anywhere in the method body — compare against `ExchangeCreateActuator.execute()` lines 120–124, which explicitly performs this step for the identical fee.
4. Sum `accountStore.getBlackhole().getBalance()` + `dynamicPropertiesStore.getBurnTrxAmount()` + `dynamicPropertiesStore.getTransactionFeePool()` before and after the withdrawal: the fee amount is missing from this total despite having been deducted from the user's account, confirming the TRX is unaccounted for.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L47-47)
```java
    long fee = calcFee();
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L91-106)
```java
      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, tokenQuant));
      } else {
        accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L118-124)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
      dynamicStore.saveLatestExchangeNum(id);
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L60-65)
```java
      adjustBalance(accountStore, ownerAddress, -(addExact(fee, amount)));
      if (dynamicStore.supportBlackHoleOptimization()) {
        dynamicStore.burnTrx(fee);
      } else {
        adjustBalance(accountStore, accountStore.getBlackhole(), fee);
      }
```
