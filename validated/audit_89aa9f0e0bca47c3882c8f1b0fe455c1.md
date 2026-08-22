### Title
Exchange spot-price manipulation via Bancor-style reserve formula, re-enabled by the "hardened" bypass of the `ExchangeTransactionContract` broadcast guard - (File: `framework/src/main/java/org/tron/core/db/Manager.java`)

### Summary
java-tron's on-chain TRC10 "Exchange" module computes trade output using a Bancor-style constant-formula against **mutable, single-block reserve balances** stored in `ExchangeCapsule` — the same class of "spot price from a manipulable AMM reserve" that the external report flags for Uniswap-style pools. Historically, `Manager.pushTransaction`/`processTransaction` rejected any incoming `ExchangeTransactionContract` from being broadcast at all (`"ExchangeTransactionContract is rejected"`), which incidentally prevented this spot-price logic from being reachable by ordinary users. However, the guard method `isExchangeTransaction` is short-circuited to `false` whenever `allowHardenExchangeCalculation` is enabled, re-opening the path for anonymous, broadcast `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract` transactions to directly manipulate exchange reserves and the resulting trade price within a single block/transaction sequence, with no TWAP or reserve-manipulation protection.

### Finding Description
`ExchangeCapsule.transaction()` computes the output amount purely from the exchange's current `firstTokenBalance`/`secondTokenBalance` (the reserves) via `ExchangeProcessor`/`SafeExchangeProcessor`, both of which implement a Bancor-style formula operating on the live balances at call time: [1](#0-0) [2](#0-1) 

This is invoked directly by `ExchangeTransactionActuator.execute`, which is reachable from a normal, unprivileged broadcast transaction (`ExchangeTransactionContract`, type 44) or via `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which let anyone alter the pool's reserves right before/after a trade: [3](#0-2) [4](#0-3) 

Because the output is computed from whatever the reserve balances happen to be at execution time (no TWAP, no minimum liquidity/slippage protection beyond a caller-supplied `expected` amount), an attacker who can sequence Inject → Transaction → Withdraw (or simply issue an oversized trade) can move the pool's implied "price" arbitrarily within the same block, exactly mirroring the reported Uniswap spot-price manipulation pattern — except here the reserves are a first-class chain-state object rather than requiring a flash loan from an external DEX.

Critically, this attack surface was previously walled off: `Manager.pushTransaction` explicitly threw `ContractValidateException("ExchangeTransactionContract is rejected")` for any transaction of that type reaching the mempool from the network/RPC layer: [5](#0-4) 

But the guard predicate `isExchangeTransaction` is gated by the `allowHardenExchangeCalculation` dynamic property, and when that property is enabled (`== 1`) the method returns `false` for `ExchangeTransactionContract`, bypassing the rejection entirely: [6](#0-5) 

The `allowHardenExchangeCalculation` flag was evidently introduced to route exchange math through the overflow-safe `SafeExchangeProcessor`/`allowHarden()` path (see `ExchangeCapsule.transaction(..., hardenedCalc)` and `AbstractExchangeActuator.allowHarden()`), but as implemented it also silently re-enables the previously-blocked, unprivileged `ExchangeTransactionContract` broadcast path — the exact reserve-based spot-price computation the report class targets.

### Impact Explanation
Once `allowHardenExchangeCalculation` is active (a network-wide dynamic parameter set by proposal, not a per-account privilege), any anonymous account can broadcast `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract` transactions to move an exchange pool's reserves and thus its Bancor-style spot price within the same block, potentially:
- Extracting value from other traders interacting with the same TRC10 exchange pair in the same block (sandwich-style manipulation).
- Corrupting the effective exchange rate that any downstream logic (e.g., wallets, dApps, or contracts) treats as a live price reference for that TRC10 pair, since java-tron itself offers no TWAP oracle for these pools.
This matches the "asset or accounting corruption" / "exchange/market math" impact classes.

### Likelihood Explanation
Likelihood depends entirely on whether `allowHardenExchangeCalculation` is turned on for the target network. If it is (which is the expected, forward-looking state per the "hardened" migration path suggested by the code/tests), the previously-hardcoded broadcast rejection is inert, and the reserve-based math becomes reachable to any address holding trivial TRX/asset balances to pay the transaction fee — no privileged role, leaked key, or malicious peer is required.

### Recommendation
Decouple the `ExchangeTransactionContract` broadcast rejection from the `allowHardenExchangeCalculation` flag: the "use safe arithmetic" concern and the "should this legacy TRC10 exchange contract type still be accepted from the network" concern are orthogonal and must not share the same toggle. If the intent is to keep `ExchangeTransactionContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract` deprecated, the rejection in `Manager.pushTransaction`/`processTransaction` should be unconditional (or gated by its own explicit property), independent of which arithmetic backend (`ExchangeProcessor` vs `SafeExchangeProcessor`) is selected.

### Proof of Concept
1. On a network where `allowHardenExchangeCalculation` is set to `1` (via the corresponding maintenance-period proposal), call `Manager.isExchangeTransaction` (indirectly through `pushTransaction`) with a signed `ExchangeTransactionContract` transaction, as reproduced in the existing unit test: [6](#0-5) 
This shows the method returns `false`, so `pushTransaction`'s `isExchangeTransaction(...)` check no longer throws, and the transaction proceeds to `ExchangeTransactionActuator.execute`.
2. From an unprivileged account, submit a large `ExchangeInjectContract`/`ExchangeTransactionContract` sequence targeting a TRC10 exchange pool with shallow reserves (e.g., the test fixtures in `ExchangeTransactionActuatorTest` use pools as small as `100000000L`/`200000000L`), driving the Bancor-style price computed by `ExchangeProcessor.exchange`/`SafeExchangeProcessor.exchange` far from its pre-trade value within the same block, then reverse the injection to restore reserves — extracting value from any victim trade executed against the manipulated price in between.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L897-899)
```java
    if (isExchangeTransaction(trx.getInstance())) {
      throw new ContractValidateException("ExchangeTransactionContract is rejected");
    }
```

**File:** framework/src/test/java/org/tron/core/db/ManagerTest.java (L1341-1367)
```java
  @Test
  public void isExchangeTransactionBypassedWhenHardenedEnabled() throws Exception {
    Transaction exchange = Transaction.newBuilder().setRawData(
        Transaction.raw.newBuilder().addContract(
            Transaction.Contract.newBuilder()
                .setType(ContractType.ExchangeTransactionContract)
                .setParameter(Any.pack(ExchangeTransactionContract.newBuilder()
                    .setExchangeId(1L).setQuant(1L).setExpected(1L).build()))
                .build())).build();

    java.lang.reflect.Method m = Manager.class.getDeclaredMethod(
        "isExchangeTransaction", Transaction.class);
    m.setAccessible(true);

    // Default: hardened disabled (==0) -> contract is treated as exchange
    chainManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    Assert.assertTrue("Exchange tx must be detected when hardened disabled",
        (boolean) m.invoke(dbManager, exchange));

    // Hardened enabled -> bypass returns false
    chainManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1);
    Assert.assertFalse("Exchange tx must be bypassed when hardened enabled",
        (boolean) m.invoke(dbManager, exchange));

    // Reset
    chainManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
  }
```
