### Title
Exchange pool invariant (non-negative reserve) check only enforced in the hardened calculation path, missing in the legacy path - ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java])

### Summary
`ExchangeCapsule.transaction()` computes new bancor-style AMM reserve balances for a TRX/token exchange pool and validates that the resulting balances stay non-negative — but this validation is only executed when `hardenedCalc` is `true`. When `hardenedCalc` is `false` (the legacy/default arithmetic path, selected whenever `allowHardenExchangeCalculation()` is not active), the same computation runs with double-precision math and the result is written back to storage with **no bound check at all**. This mirrors the Blend finding precisely: a critical invariant (`require_utilization_below_max` in Blend; non-negative pool balance here) is enforced on one code path but missing on a functionally identical path that can violate the same invariant.

### Finding Description
In `ExchangeCapsule.transaction()`: [1](#0-0) 

the reserve-balance invariant check is guarded by the `hardenedCalc` flag:
```java
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
```
`hardenedCalc` is derived from `allowHarden()`, which reads the chain parameter `allowHardenExchangeCalculation` from `DynamicPropertiesStore`: [2](#0-1) 

This is a committee-controlled proposal flag (`AllowHardenExchangeCalculation`), so it is off by default until activated on chain, exactly the same class of "optional/committee-gated hardening" pattern seen elsewhere in the codebase (`allowStrictMath`, `hardenResourceCalculation`, etc.). While the flag is off, `ExchangeCapsule.transaction()` uses `ExchangeProcessor` (double-precision bancor math): [3](#0-2) 

and the resulting `newFirstTokenBalance`/`newSecondTokenBalance` are stored unconditionally, without any check that they remain `>= 0` or within any sane bound: [4](#0-3) 

This function is invoked directly from `ExchangeTransactionActuator.execute()`, a fully unprivileged, user-triggerable contract (`ExchangeTransactionContract`): [5](#0-4) 

Because the legacy (`ExchangeProcessor`) math uses floating point (`double`) for the bancor exponentiation and division, extreme or precision-loss inputs (e.g., very large `sellTokenQuant` relative to pool reserves, or reserves near zero) can produce a computed `buyTokenQuant` that exceeds the corresponding pool reserve, driving `newFirstTokenBalance` or `newSecondTokenBalance` negative. Unlike the hardened path, the legacy path never rejects this — it silently persists a corrupted, negative reserve into the `Exchange`/`ExchangeV2` store via `Commons.putExchangeCapsule(...)`.

The very existence of the `hardenedCalc`-gated check, plus the dedicated `SafeExchangeProcessor` (BigDecimal-based, always calling this check when used), strongly indicates the negative-balance/invariant-violation scenario is a real, previously-recognized risk in the legacy arithmetic that this hardening was built specifically to close — but the fix is opt-in via governance rather than uniformly enforced.

### Impact Explanation
If the `AllowHardenExchangeCalculation` parameter is not enabled (or on any network — testnet/private chain/forked chain based on this code — where it defaults off), an unprivileged user submitting an `ExchangeTransactionContract` can corrupt an on-chain TRX/TRC-10 exchange pool's reserve accounting by driving one side's balance negative. This:
- Permanently invalidates future bancor-formula pricing calculations for that exchange (all subsequent trades operate on corrupted state).
- Can be leveraged to extract more value from the pool than it holds (analogous to draining backstop/liabilities beyond available supply in the Blend bug), impacting other users of the same exchange pool.
- Represents a state/accounting divergence rather than a purely cosmetic issue, since `firstTokenBalance`/`secondTokenBalance` are directly used for subsequent trade pricing and for `ExchangeWithdrawActuator` payouts.

### Likelihood Explanation
Reachable by any unprivileged account via a standard `ExchangeTransactionContract` transaction — no special role required, matching the "unprivileged-user analog" requirement. The trigger condition depends on the `allowHardenExchangeCalculation` governance flag being inactive; since this is a legacy/default code path (activated only by committee vote), it is a realistic, non-theoretical exposure on any deployment where the proposal has not been passed (or has not yet propagated), and on any fork/testnet initialized from this codebase without the flag pre-enabled.

### Recommendation
Remove the `hardenedCalc` gate on the invariant check in `ExchangeCapsule.transaction()` so that `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` is validated unconditionally, regardless of which processor (`ExchangeProcessor` or `SafeExchangeProcessor`) computed the result. This aligns with the Blend mitigation approach: apply the bound-violation check to every code path that can alter the invariant-relevant state, not only the "upgraded"/hardened one.

### Proof of Concept
1. Ensure `allowHardenExchangeCalculation` is not active (default state) so `ExchangeTransactionActuator.execute()` calls `exchangeCapsule.transaction(tokenID, tokenQuant, dynamicStore.allowStrictMath(), allowHarden())` with `hardenedCalc = false`.
2. Create/target an `Exchange` pool with a small reserve on one side (e.g. via `ExchangeCreateActuator` or an existing low-liquidity pool), as illustrated by the pool setups in: [6](#0-5) 
3. Submit an `ExchangeTransactionContract` selling a very large `tokenQuant` relative to the pool's reserves, causing the bancor `exchangeFromSupply` double-precision computation in `ExchangeProcessor.exchange()` to return a `buyTokenQuant` at or exceeding the current opposite-side reserve.
4. Because `hardenedCalc` is `false`, the `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` check in `ExchangeCapsule.transaction()` (lines 160-162) never executes, and the negative balance is committed via `Commons.putExchangeCapsule(...)` in `ExchangeTransactionActuator.execute()` (lines 93-96), corrupting the pool's on-chain state.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```

**File:** framework/src/test/java/org/tron/core/capsule/ExchangeCapsuleTest.java (L108-128)
```java
  @Test
  public void testExchange() throws ContractValidateException {
    long sellBalance = 100000000L;
    long buyBalance = 100000000L;

    byte[] key = ByteArray.fromLong(1);

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = chainBaseManager.getExchangeStore().get(key);
      exchangeCapsule.setBalance(sellBalance, buyBalance);

      long sellQuant = 1_000_000L;
      byte[] sellID = "abc".getBytes();
      boolean useStrictMath = chainBaseManager.getDynamicPropertiesStore().allowStrictMath();
      long result = exchangeCapsule.transaction(sellID, sellQuant, useStrictMath);
      Assert.assertEquals(990_099L, result);
      sellBalance += sellQuant;
      Assert.assertEquals(sellBalance, exchangeCapsule.getFirstTokenBalance());
      buyBalance -= result;
      Assert.assertEquals(buyBalance, exchangeCapsule.getSecondTokenBalance());
```
