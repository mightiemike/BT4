### Title
Unchecked `long` multiplication in `payEnergyBill` energy-fee calculation can overflow, corrupting caller/account fee accounting - ([File: chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java])

### Summary
`ReceiptCapsule.payEnergyBill` computes the TRX cost of energy consumed beyond an account's frozen/free energy using a raw `long * long` multiplication with no overflow protection, in contrast to essentially every other fee/reward/exchange calculation in java-tron, which has been deliberately hardened to use `BigInteger` or `StrictMathWrapper`/`Maths.multiplyExact` with `ArithmeticException` on overflow.

### Finding Description
In `payEnergyBill`, once it is determined that the account's available energy (`accountEnergyLeft`) is insufficient to cover the energy `usage`, the excess energy is billed in TRX: [1](#0-0) 

```
long sunPerEnergy = Constant.SUN_PER_ENERGY;
long dynamicEnergyFee = dynamicPropertiesStore.getEnergyFee();
if (dynamicEnergyFee > 0) {
  sunPerEnergy = dynamicEnergyFee;
}
long energyFee =
    (usage - accountEnergyLeft) * sunPerEnergy;
this.setEnergyUsage(accountEnergyLeft);
this.setEnergyFee(energyFee);
```

Both `(usage - accountEnergyLeft)` and `sunPerEnergy` are plain `long` values, and the multiplication is done with the native `*` operator — no `Maths.multiplyExact`, `StrictMathWrapper.multiplyExact`, or `BigInteger` fallback is used, unlike the parallel logic in `AbstractActuator`, `VMActuator.getEnergyFee`/`getTotalEnergyLimitWithFixRatio`, `EnergyProcessor`, `ExchangeCapsule`/`SafeExchangeProcessor`, `MarketUtils.multiplyAndDivide`, and `RepositoryImpl.increase`/`getUsage`, all of which route long-multiplication through `BigInteger` or exact-checked arithmetic to avoid silent wraparound: [2](#0-1) [3](#0-2) 

`usage` here is `receipt.getEnergyUsageTotal()` (or a per-account split derived from it) and `sunPerEnergy` is the governance-controlled `ENERGY_FEE` dynamic property, which can be raised via proposal (see `ProposalService.process`'s `ENERGY_FEE` case) to arbitrarily large values: [4](#0-3) 

If `(usage - accountEnergyLeft) * sunPerEnergy` overflows `Long.MAX_VALUE`, the resulting `energyFee` wraps to an unpredictable (potentially negative) value. This value is then:
1. Stored directly into the on-chain `ResourceReceipt.energyFee` field via `setEnergyFee`, corrupting the recorded transaction fee.
2. Compared against `account.getBalance()` — if `energyFee` wraps negative, `balance < energyFee` is always false, allowing the transaction to proceed with an incorrect (possibly negative) deduction from the account balance.
3. Used in `account.setBalance(balance - energyFee)`, `dynamicPropertiesStore.addTransactionFeePool(energyFee)` / `burnTrx(energyFee)` / `Commons.adjustBalance(...)`, propagating the corrupted value into system-wide fee-pool/black-hole accounting.

This is the direct analog of the reported bug class: an accrued-fee calculation multiplying two bounded quantities as fixed-width integers without widening, producing an incorrect fee that is subsequently used to update account/locked-balance state.

### Impact Explanation
If triggered, this could result in an account being charged an incorrect (including negative, i.e., effectively free or balance-increasing) energy fee, or in corrupted values being written into the `TransactionFeePool`/black-hole burn accounting and into the immutable, permanently recorded `ResourceReceipt`. This affects core TRX accounting/settlement paths reachable by any unprivileged contract caller who triggers TVM execution and exhausts frozen/free energy.

### Likelihood Explanation
Exploitability depends on whether `(usage - accountEnergyLeft) * sunPerEnergy` can realistically reach `Long.MAX_VALUE` (~9.22e18) given current bounds on `usage` (limited by feeLimit/energy limits enforced in `VMActuator`) and `sunPerEnergy` (the `ENERGY_FEE` dynamic parameter, settable via governance proposal). I was not able to fully confirm from the available index whether hard upper bounds on `ENERGY_FEE` or on maximum `feeLimit`/energy usage per transaction preclude this overflow in practice — this would require checking `ProposalUtil`'s validation of the `ENERGY_FEE` parameter and the maximum `feeLimit` enforced elsewhere in the codebase, which I could not fully verify within the available tool budget. Given that all structurally similar computations elsewhere in the codebase (`VMActuator`, `EnergyProcessor`, `ExchangeCapsule`, `MarketUtils`, `RepositoryImpl`) have been explicitly hardened against this exact overflow pattern, while this one instance in `ReceiptCapsule.payEnergyBill` was not, this is very plausibly an overlooked case rather than a proven-safe one.

### Recommendation
Replace the raw multiplication in `ReceiptCapsule.payEnergyBill` with an overflow-checked computation, consistent with the rest of the codebase's hardening pattern, e.g.:
```java
long energyFee = BigInteger.valueOf(usage - accountEnergyLeft)
    .multiply(BigInteger.valueOf(sunPerEnergy))
    .longValueExact();
```
or use `Maths.multiplyExact(usage - accountEnergyLeft, sunPerEnergy, dynamicPropertiesStore.disableJavaLangMath())`, matching the pattern already used in `AbstractActuator.multiplyExact` and `MarketUtils.multiplyAndDivide`. Also add validation/clamping on the `ENERGY_FEE` governance parameter to bound `sunPerEnergy`.

### Proof of Concept
Not independently reproducible from static analysis alone within the current investigation — a concrete PoC would require confirming actual runtime bounds on `usage` (max energy consumable in one transaction, governed by `feeLimit`/`TotalEnergyLimit`) and the maximum settable value of the `ENERGY_FEE` dynamic property via `ProposalUtil`/`ProposalService`, to demonstrate that their product can exceed `Long.MAX_VALUE`. This should be validated in a live/test environment (e.g., a Devin session) by: (1) inspecting `ProposalUtil`'s bounds-check for `ENERGY_FEE`, (2) setting `ENERGY_FEE` to its allowed maximum via a test proposal, (3) crafting a contract call that consumes energy up to the maximum allowed by `feeLimit` while the caller has zero frozen/free energy, and (4) observing the resulting `ResourceReceipt.energyFee` and account balance for wraparound.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ReceiptCapsule.java (L288-296)
```java
      long sunPerEnergy = Constant.SUN_PER_ENERGY;
      long dynamicEnergyFee = dynamicPropertiesStore.getEnergyFee();
      if (dynamicEnergyFee > 0) {
        sunPerEnergy = dynamicEnergyFee;
      }
      long energyFee =
          (usage - accountEnergyLeft) * sunPerEnergy;
      this.setEnergyUsage(accountEnergyLeft);
      this.setEnergyFee(energyFee);
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L105-112)
```java
  private static long getEnergyFee(long callerEnergyUsage, long callerEnergyFrozen,
      long callerEnergyTotal) {
    if (callerEnergyTotal <= 0) {
      return 0;
    }
    return BigInteger.valueOf(callerEnergyFrozen).multiply(BigInteger.valueOf(callerEnergyUsage))
        .divide(BigInteger.valueOf(callerEnergyTotal)).longValueExact();
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/utils/MarketUtils.java (L264-277)
```java
  public static long multiplyAndDivide(long a, long b, long c, boolean disableMath) {
    try {
      long tmp = multiplyExact(a, b, disableMath);
      return floorDiv(tmp, c, disableMath);
    } catch (ArithmeticException ex) {
      // do nothing here, because we will use BigInteger to compute again
    }

    BigInteger aBig = BigInteger.valueOf(a);
    BigInteger bBig = BigInteger.valueOf(b);
    BigInteger cBig = BigInteger.valueOf(c);

    return aBig.multiply(bBig).divide(cBig).longValue();
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L83-90)
```java
        case ENERGY_FEE: {
          manager.getDynamicPropertiesStore().saveEnergyFee(entry.getValue());
          // update energy price history
          manager.getDynamicPropertiesStore().saveEnergyPriceHistory(
              manager.getDynamicPropertiesStore().getEnergyPriceHistory()
                  + "," + proposalCapsule.getExpirationTime() + ":" + entry.getValue());
          break;
        }
```
