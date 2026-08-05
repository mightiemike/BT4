## Title
Legacy (non-hardened) Exchange bonding-curve math permits negative pool balances to be persisted uncorrected in `ExchangeCapsule.transaction()` - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
The Derby finding is that deltas/allocations are allowed to become negative and are then committed to storage via unchecked casts, permanently corrupting protocol accounting. The equivalent pattern in java-tron is `ExchangeCapsule.transaction()`, which computes `newFirstTokenBalance`/`newSecondTokenBalance` from a bonding-curve calculation and only validates that these are non-negative when the `hardenedCalc` flag is `true`. When `hardenedCalc` is `false` (the legacy/default path, controlled by the `AllowHardenExchangeCalculation` chain parameter), the negative-balance check is skipped entirely, and the corrupted balances are written straight into the persisted `Exchange` protobuf.

### Finding Description
In `ExchangeCapsule.transaction()`: [1](#0-0) 

the balances are computed either through `SafeExchangeProcessor` (hardened) or the legacy `ExchangeProcessor` (non-hardened), and the resulting `newFirstTokenBalance` / `newSecondTokenBalance` are validated for negativity only inside `if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0))`. When `hardenedCalc` is `false`, no such check runs before `this.exchange = this.exchange.toBuilder().setFirstTokenBalance(newFirstTokenBalance).setSecondTokenBalance(newSecondTokenBalance).build();` persists whatever value was computed — exactly the pattern flagged in the Derby report where a computed delta/allocation is written to state without verifying it stayed within a valid (non-negative) range.

`allowHarden()` in `AbstractExchangeActuator` is gated by the `AllowHardenExchangeCalculation` dynamic property: [2](#0-1) 
so unless/until that proposal is activated on-chain, every call from `ExchangeTransactionActuator.execute()`/`doValidate()` and `ExchangeWithdrawActuator` runs the legacy, unchecked path: [3](#0-2) [4](#0-3) 

`doValidate()` also invokes `exchangeCapsule.transaction(...)` speculatively to compute `anotherTokenQuant` for the "expected" check — meaning even `validate()` runs the unguarded legacy math against attacker-controlled `tokenQuant`, and `execute()` re-runs it and commits the result.

The legacy math itself (`ExchangeProcessor.exchangeFromSupply`) uses floating-point `Math.pow` on a supply value that is mutated as a `long` field across calls (`exchangeToSupply`/`exchangeFromSupply`), and casts a `double` to `long` for `out`/`exchangeBalance`: [5](#0-4) 
Precision loss/negative-exponent edge cases in this floating-point curve (e.g., very small `newBalance`, or repeated extreme trades) can, in principle, drive `buyTokenQuant` beyond what the reserve pool actually holds, which is precisely the scenario the `newSecondTokenBalance < 0` / `newFirstTokenBalance < 0` guard exists to catch — but that guard is bypassed on the legacy path.

### Impact Explanation
If a legacy-path trade drives a reserve balance negative, that value is committed to the `Exchange`/`ExchangeV2` store unconditionally via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()`. A negative pool balance corrupts the AMM's accounting invariants permanently: subsequent trades reading `firstTokenBalance`/`secondTokenBalance` as inputs to the bonding-curve formula will produce nonsensical (potentially further-corrupted or even favorably-exploitable) exchange rates, and withdrawals via `ExchangeWithdrawActuator` compute `anotherTokenQuant` from these already-corrupted balances, allowing accounting divergence/fund-loss scenarios that cannot be reversed without a manual chain intervention. This matches the "invalid-state/divergence" impact class from the Derby report.

### Likelihood Explanation
Reachability is fully unprivileged: any account can call `ExchangeTransactionActuator` against any active exchange pair with attacker-chosen `tokenQuant`, since the only preconditions are having enough TRX/asset balance and the pool having non-zero balances. The negative-balance guard is present in the codebase (proving the team is aware of the exact risk class) but is explicitly conditioned on `hardenedCalc`/`allowHarden()`, i.e., on a chain-parameter proposal (`AllowHardenExchangeCalculation`) that must be voted in separately. On any network/period where that proposal has not been activated (the default state, and the reason a "legacy vs hardened" code path exists at all), the vulnerable path is live. I could not verify from the available index whether `AllowHardenExchangeCalculation` is already activated on TRON mainnet; if it has not been activated, likelihood is high given ordinary user-level access is sufficient. If it has been fully activated network-wide with no way to disable it, the legacy branch becomes dead code and the practical likelihood drops to none — this is the key open question I could not resolve from the indexed code alone.

### Recommendation
Apply the non-negative-balance check unconditionally in `ExchangeCapsule.transaction()`, regardless of `hardenedCalc`:
```java
if (newFirstTokenBalance < 0 || newSecondTokenBalance < 0) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
```
rather than gating it behind `hardenedCalc &&`. This mirrors the Derby fix recommendation: validate the resulting accounting values before persisting them, independent of any feature flag, rather than only in the "hardened" path.

### Proof of Concept
Conceptual PoC (cannot be fully executed without the live value of `AllowHardenExchangeCalculation` and precise floating-point edge inputs, which would require running the actual `ExchangeProcessor` curve math against a live/test node):
1. Confirm on the target chain that `getAllowHardenExchangeCalculation() == 0` (legacy path active) via `DynamicPropertiesStore`.
2. Create/find an `Exchange` pair with a very small reserve on one side (e.g., trading down toward exhaustion in prior legacy trades, since no floor is enforced pre-hardening).
3. Submit an `ExchangeTransactionContract` with a `tokenQuant` sized so that `ExchangeProcessor.exchange()`'s floating-point bonding curve returns a `buyTokenQuant` computed from `exchangeFromSupply` that exceeds the current opposite-side reserve.
4. Observe that `ExchangeCapsule.transaction()` (legacy branch) commits `newSecondTokenBalance = secondTokenBalance - buyTokenQuant` (or vice versa) as negative, since the `hardenedCalc && ...` guard does not fire, and this value is persisted via `Commons.putExchangeCapsule`.
5. Subsequent calls to `exchangeCapsule.transaction()` for this pool now operate on a permanently negative reserve, corrupting all future rate calculations for that exchange pair.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-169)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

    long buyTokenQuant = 0;
    long firstTokenBalance = this.exchange.getFirstTokenBalance();
    long secondTokenBalance = this.exchange.getSecondTokenBalance();
    long newFirstTokenBalance;
    long newSecondTokenBalance;

    if (this.exchange.getFirstTokenId().equals(ByteString.copyFrom(sellTokenID))) {
      buyTokenQuant = processor.exchange(firstTokenBalance,
          secondTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
          : firstTokenBalance + sellTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(secondTokenBalance, buyTokenQuant)
          : secondTokenBalance - buyTokenQuant;

    } else {
      buyTokenQuant = processor.exchange(secondTokenBalance,
          firstTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(firstTokenBalance, buyTokenQuant)
          : firstTokenBalance - buyTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(secondTokenBalance, sellTokenQuant)
          : secondTokenBalance + sellTokenQuant;

    }

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();

    return buyTokenQuant;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L67-69)
```java
      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```
