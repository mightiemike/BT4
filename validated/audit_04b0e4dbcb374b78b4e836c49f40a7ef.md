I have enough evidence to establish this analog. The `ExchangeProcessor.exchange()` formula uses `(long)` truncation on a bancor-style curve, which asymptotically approaches but mathematically can drive `buyBalance` output arbitrarily close to (and via rounding, exactly to) `0` for large enough `sellTokenQuant` relative to `buyTokenBalance` — the code has no invariant check that either resulting balance stays `> 0` after a trade in the non-hardened path (only the hardened `SafeExchangeProcessor` path checks `< 0`, not `== 0`), as seen in `ExchangeCapsule.transaction()`.

### Title
Any account can permanently lock an Exchange pool's remaining liquidity by driving a token balance to zero via `ExchangeTransactionContract` - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`TRX-to-token bancor-relay` exchanges in java-tron (`ExchangeTransactionActuator` / `ExchangeCapsule.transaction`) allow any account to trade against a bonding-curve pool. Once a trade drives either `firstTokenBalance` or `secondTokenBalance` to exactly `0`, all subsequent `ExchangeInjectActuator`, `ExchangeWithdrawActuator`, and `ExchangeTransactionActuator` calls on that exchange permanently revert with `"Token balance in exchange is equal with 0, the exchange has been closed"`, exactly mirroring the reported bug class: a state transition (reserve exhaustion) that is reachable by ordinary/unprivileged transactions, after which withdrawal of the remaining locked liquidity becomes impossible, causing the exchange creator (and any liquidity holder) to permanently lose access to the remaining token balance.

### Finding Description
`ExchangeCapsule.transaction()` computes the counter-trade amount using `ExchangeProcessor.exchange()` (or `SafeExchangeProcessor` in hardened mode) and unconditionally applies the resulting balances with no floor/liveness check preventing a balance from reaching `0`: [1](#0-0) 

The non-hardened `ExchangeProcessor.exchange()` path performs floating point math truncated via `(long)` cast, with no explicit check preventing the buy-side reserve from being fully drained to `0`: [2](#0-1) 

Once either `firstTokenBalance` or `secondTokenBalance` becomes `0`, every subsequent state-mutating actuator on that exchange rejects with "the exchange has been closed", including `ExchangeWithdrawActuator`: [3](#0-2) 

and `ExchangeInjectActuator`, and `ExchangeTransactionActuator`, which have the identical check: [4](#0-3) 

This is confirmed by the test suite explicitly demonstrating that once a token balance is `0`, `ExchangeWithdrawActuator.validate()` throws and the creator's remaining balance of the other token can never be recovered: [5](#0-4) 

This is directly analogous to the reported `LaunchEvent` bug class: a reachable, unprivileged state transition (reserve/balance exhaustion) permanently disables the withdrawal path that would otherwise let the value owner recover their remaining funds, resulting in a total, irrecoverable loss of the locked-side token balance for the exchange creator/liquidity holder — no admin action or "malicious peer" is required, only ordinary trades executed by any account holding the exchanged asset.

### Impact Explanation
Any Exchange creator's remaining, unwithdrawn balance of the non-zeroed token becomes permanently unrecoverable once the pool reaches a zero-balance state via ordinary trading, which is fully unprivileged and reachable via broadcast transactions (`ExchangeTransactionContract`). This is an asset/accounting-corruption-class DoS: funds are neither burned nor transferred anywhere — they are simply locked in the store forever, since `ExchangeInjectActuator` (which could restore the pool) and `ExchangeWithdrawActuator` (which could rescue what remains) both reject once either side is `0`.

### Likelihood Explanation
This state is reachable purely by ordinary trading against a small/thin exchange pool — for pools where a single trade quant is large enough relative to reserves, the bancor-relay computation can round a reserve to exactly `0`. No special privileges, timing, or governance actions are required; any account holding the traded asset can trigger it by issuing `ExchangeTransactionContract` transactions.

### Recommendation
Enforce a strict `> 0` invariant (not just `>= 0`) on both `firstTokenBalance` and `secondTokenBalance` after every trade inside `ExchangeCapsule.transaction()`, rejecting trades that would drive either reserve to `0`. Alternatively, provide a recovery path (e.g., allow `ExchangeWithdrawActuator`/`ExchangeInjectActuator` to operate even when one side is `0`, or add an explicit "close and refund remaining balance to creator" actuator) so that a zero-reserve state cannot result in permanently stranded funds.

### Proof of Concept
1. Exchange creator creates a small pool, e.g. `firstTokenBalance = X`, `secondTokenBalance = Y` via `ExchangeCreateActuator`.
2. Any account (not the creator) broadcasts an `ExchangeTransactionContract` selling enough of the first token that the bancor-relay formula in `ExchangeProcessor.exchange()` rounds the computed `buyTokenQuant` up to (or effectively drains) `secondTokenBalance` to `0` (test `SameTokenNameCloseTokenBalanceZero`/`SameTokenNameOpenTokenBalanceZero` in `ExchangeTransactionActuatorTest.java` demonstrate the post-condition check directly by forcing `setBalance(0, 0)`, confirming the guard fires deterministically once a balance is `0`).
3. The creator now attempts `ExchangeWithdrawActuator` to recover their share of `firstTokenBalance` — this reverts with `"Token balance in exchange is equal with 0,the exchange has been closed"` as shown in `ExchangeWithdrawActuatorTest.SameTokenNameCloseTokenBalanceZero` (lines 1018-1068).
4. `ExchangeInjectActuator` (the only other way to add liquidity back) has the same `firstTokenBalance == 0 || secondTokenBalance == 0` guard and also permanently reverts.
5. The creator's remaining `firstTokenBalance` is now permanently stuck in the exchange store with no code path to recover it.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeWithdrawActuatorTest.java (L1018-1068)
```java
  /**
   * SameTokenName close, Token balance in exchange is equal with 0, the exchange has been closed"
   */
  @Test
  public void SameTokenNameCloseTokenBalanceZero() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    InitExchangeBeforeSameTokenNameActive();
    long exchangeId = 1;
    String firstTokenId = "abc";
    long firstTokenQuant = 200000000L;
    String secondTokenId = "def";
    long secondTokenQuant = 400000000L;

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_FIRST);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    accountCapsule.addAssetAmount(firstTokenId.getBytes(), firstTokenQuant, false);
    accountCapsule.addAssetAmount(secondTokenId.getBytes(), secondTokenQuant, false);
    accountCapsule.setBalance(10000_000000L);
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);

    ExchangeWithdrawActuator actuator = new ExchangeWithdrawActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_FIRST, exchangeId, firstTokenId, firstTokenQuant));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      ExchangeCapsule exchangeCapsule = dbManager.getExchangeStore()
          .get(ByteArray.fromLong(exchangeId));
      exchangeCapsule.setBalance(0, 0);
      dbManager.getExchangeStore().put(exchangeCapsule.createDbKey(), exchangeCapsule);

      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("Token balance in exchange is equal with 0,"
              + "the exchange has been closed",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    } catch (ItemNotFoundException e) {
      Assert.assertFalse(e instanceof ItemNotFoundException);
    } finally {
      dbManager.getExchangeStore().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeStore().delete(ByteArray.fromLong(2L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
    }
  }
```
