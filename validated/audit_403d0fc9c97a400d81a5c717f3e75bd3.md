### Title
Unchecked (non-SafeMath) arithmetic in exchange balance updates when `allowHardenExchangeCalculation` is disabled - (File: `actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java`)

### Summary
`AbstractExchangeActuator`, the shared base class for `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator`, overrides the overflow-checked `addExact`/`subtractExact` helpers inherited from `AbstractActuator` with a version that performs raw, unchecked `long` addition/subtraction unless a governance-controlled flag `allowHardenExchangeCalculation` is enabled. [1](#0-0) 

### Finding Description
`AbstractActuator` (the generic actuator superclass) always performs overflow-checked math via `Maths.addExact`/`subtractExact`, which internally delegates to either `StrictMathWrapper` or `MathWrapper`, both of which are checked, overflow-throwing implementations. [2](#0-1) [3](#0-2) 

However, `AbstractExchangeActuator` shadows these methods for the entire exchange (Bancor-style TRX/TRC10 swap) family of actuators, and, unless the dynamic property `allowHardenExchangeCalculation` is turned on, falls back to plain, non-checked `x + y` / `x - y`: [4](#0-3) 

These shadowed, unchecked operations are used to mutate the on-chain exchange pool balances and account balances directly inside `execute()` for user-broadcastable transactions:
- `ExchangeInjectActuator.execute()` uses `addExact`/`subtractExact` (the exchange-actuator overridden, unchecked-by-default versions) to update `firstTokenBalance`/`secondTokenBalance` and the owner's TRX balance. [5](#0-4) 
- `ExchangeWithdrawActuator.execute()` does the same for withdrawal accounting. [6](#0-5) 

By contrast, `validate()` in these actuators computes the "expected" values using exact `BigInteger`/`BigDecimal` arithmetic (`bigFirstTokenBalance.multiply(...).divide(...)`), so the safety check performed at validation time is precise, while the actual state mutation performed at execution time can silently overflow/underflow if the raw-math path is taken. [7](#0-6) 

This mirrors the reported bug class exactly: production code performing raw arithmetic on user-controlled financial quantities without a SafeMath-equivalent guard, where the "fix" (checked math) exists but is gated behind an opt-in switch rather than being the default behavior.

### Impact Explanation
If `allowHardenExchangeCalculation` is not enabled (its default/initial state, based on it being a `ProposalUtil`-managed, governance-activated dynamic parameter rather than a hardcoded genesis default) any of the three exchange actuators will update pool/account balances using raw `long` addition/subtraction. An overflow or underflow here would corrupt `ExchangeCapsule` pool balances or `AccountCapsule` TRX balances, i.e., asset/accounting corruption in the on-chain TRX/TRC10 exchange market — reachable purely by broadcasting `ExchangeInjectContract`, `ExchangeWithdrawContract`, or `ExchangeTransactionContract` transactions from any account, with no privileged access required.

### Likelihood Explanation
Exploitability depends on whether the effective exchange balances/quantities involved can reach near `Long.MAX_VALUE`/`Long.MIN_VALUE` before the raw addition/subtraction executes. `validate()` enforces `newTokenBalance > balanceLimit` checks using the dynamic property `getExchangeBalanceLimit()` computed via exact BigInteger math, which likely constrains this in most configurations. I was not able to confirm the exact default value of `getExchangeBalanceLimit()` or the exact default/activation status of `allowHardenExchangeCalculation` within the available indexed content, so I cannot fully confirm whether an attacker can practically drive balances to the overflow boundary under default network parameters — this reduces confidence in immediate practical exploitability, though the code path itself is a genuine, reachable divergence between "safe" validation and "unsafe" execution math.

### Recommendation
Remove the `allowHarden()` gate in `AbstractExchangeActuator` and make the checked-math path (`StrictMathWrapper.addExact`/`subtractExact`) unconditional for all exchange actuators, matching the behavior already used by `AbstractActuator` for non-exchange actuators. If backward compatibility (consensus continuity) requires keeping the flag for a hard-fork activation window, ensure `allowHardenExchangeCalculation` is activated by default in all new/updated genesis and network configurations, and audit all historical states to confirm no accounting drift occurred while the flag was off.

### Proof of Concept
Not independently reproducible from the indexed code alone since it depends on the live value of the `EXCHANGE_BALANCE_LIMIT` dynamic parameter and the `allowHardenExchangeCalculation` activation status, both of which I could not fully confirm. Conceptually: repeatedly call `ExchangeInjectContract`/`ExchangeTransactionContract` to grow one side of an exchange pool's balance to just under `Long.MAX_VALUE` (bounded only by `getExchangeBalanceLimit()`), then issue one more inject/transaction that causes `addExact(firstTokenBalance, tokenQuant)` (the unchecked, exchange-actuator-overridden version) to wrap around, corrupting the pool's recorded balance — the existing test suite already demonstrates the intended checked behavior only manifests when `saveAllowHardenExchangeCalculation(1)` is explicitly set, e.g. in `ExchangeInjectActuatorTest.hardenedAddExactOverflowThrows()`. [8](#0-7)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-23)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }

  public long subtractExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.subtractExact(x, y) : x - y;
  }

  public long addExact(long x, long y) {
    return allowHarden() ? StrictMathWrapper.addExact(x, y) : x + y;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractActuator.java (L60-90)
```java
  public long addExact(long x, long y) {
    return Maths.addExact(x, y, this.disableJavaLangMath());
  }

  public long addExact(int x, int y) {
    return Maths.addExact(x, y, this.disableJavaLangMath());
  }

  public long floorDiv(long x, long y) {
    return Maths.floorDiv(x, y, this.disableJavaLangMath());
  }

  public long floorDiv(long x, int y) {
    return this.floorDiv(x, (long) y);
  }

  public long multiplyExact(long x, long y) {
    return Maths.multiplyExact(x, y, this.disableJavaLangMath());
  }

  public long multiplyExact(long x, int y) {
    return this.multiplyExact(x, (long) y);
  }

  public int multiplyExact(int x, int y) {
    return Maths.multiplyExact(x, y, this.disableJavaLangMath());
  }

  public long subtractExact(long x, long y) {
    return Maths.subtractExact(x, y, this.disableJavaLangMath());
  }
```

**File:** common/src/main/java/org/tron/common/math/Maths.java (L28-50)
```java
  public static long addExact(long x, long y, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.addExact(x, y) : MathWrapper.addExact(x, y);
  }

  public static int addExact(int x, int y, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.addExact(x, y) : MathWrapper.addExact(x, y);
  }

  public static long floorDiv(long x, long y, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.floorDiv(x, y) : MathWrapper.floorDiv(x, y);
  }

  public static int multiplyExact(int x, int y,  boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.multiplyExact(x, y) : MathWrapper.multiplyExact(x, y);
  }

  public static long multiplyExact(long x, long y,  boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.multiplyExact(x, y) : MathWrapper.multiplyExact(x, y);
  }

  public static long subtractExact(long x, long y, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.subtractExact(x, y) : MathWrapper.subtractExact(x, y);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-99)
```java
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L209-227)
```java
    BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
    BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
    BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
    long newTokenBalance;
    long newAnotherTokenBalance;

    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-104)
```java
      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, tokenQuant),
            subtractExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, anotherTokenQuant),
            subtractExact(secondTokenBalance, tokenQuant));
      }

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
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeInjectActuatorTest.java (L1862-1899)
```java
  @Test
  public void hardenedAddExactOverflowThrows() throws Exception {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(1);
    dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(1);
    InitExchangeSameTokenNameActive();

    // Corrupt pool balance to near-MAX so addExact overflows on inject.
    long exchangeId = 1;
    ExchangeCapsule pool = dbManager.getExchangeV2Store().get(ByteArray.fromLong(exchangeId));
    pool.setBalance(Long.MAX_VALUE - 10L, 200000000L);
    dbManager.getExchangeV2Store().put(pool.createDbKey(), pool);

    String firstTokenId = "123";
    AssetIssueCapsule a1 = new AssetIssueCapsule(
        AssetIssueContract.newBuilder()
            .setName(ByteString.copyFrom(firstTokenId.getBytes())).build());
    a1.setId(String.valueOf(1L));
    dbManager.getAssetIssueStore().put(a1.getName().toByteArray(), a1);

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmountV2(firstTokenId.getBytes(), 1000000000L,
        dbManager.getDynamicPropertiesStore(), dbManager.getAssetIssueStore());
    accountCapsule.setBalance(10000_000000L);
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);

    ExchangeInjectActuator actuator = new ExchangeInjectActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, exchangeId, firstTokenId, 1000000000L));
    try {
      Assert.assertThrows(ContractExeException.class,
          () -> actuator.execute(new TransactionResultCapsule()));
    } finally {
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
      dbManager.getDynamicPropertiesStore().saveAllowHardenExchangeCalculation(0);
    }
  }
```
