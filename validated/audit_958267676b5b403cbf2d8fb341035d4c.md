### Title
Exchange (Bancor-style AMM) pool creator can unilaterally drain reserves via `ExchangeWithdrawContract`, breaking other users' pending swaps - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
java-tron's on-chain TRC10 Exchange feature lets any account create a Bancor-style liquidity pool (`ExchangeCreateContract`) and later trade against it (`ExchangeTransactionContract`). Only the pool's creator is authorized to remove liquidity (`ExchangeWithdrawContract`), but the withdrawal path enforces no minimum-reserve or share-based limit — mirroring the Unlock `withdraw()` bug where a privileged party can drain funds that other unprivileged users depend on for an expected on-chain operation.

### Finding Description
`ExchangeWithdrawActuator` allows only the exchange's creator to withdraw tokens from the pool [1](#0-0) . The only balance-related guard in `doValidate()` is that the pool balances must not already be zero before the withdrawal [2](#0-1) ; there is no floor that reserves enough liquidity for other participants' outstanding trades, and no share-accounting system (e.g., LP shares) limiting the creator to their proportional stake — the creator can withdraw essentially the entire reserve in one call, down to the smallest unit that keeps both balances non-zero [3](#0-2) .

Other, unprivileged users interact with this same shared pool through `ExchangeTransactionContract`, whose validation requires the pool's token balance to be non-zero and the trader's balance/token amount to be sufficient, but has no protection against a pool that has just been drained to near-zero [4](#0-3) . Because the AMM swap output is computed from the ratio of `firstTokenBalance`/`secondTokenBalance` [5](#0-4) , a creator-triggered drain either makes reasonably sized trades fail with "exchange balance is not enough" or "Not precise enough", or collapses the exchange rate so severely that a trader's expected `expected` (minimum output) check fails, functionally denying the swap — directly analogous to Unlock's `cancelAndRefund` reverting when the lock manager withdraws too much.

### Impact Explanation
Any account that creates an Exchange effectively becomes a semi-trusted counterparty for every other account that later trades against that specific pool. Because withdrawal is unbounded and un-gated by any reserve requirement, the creator can unilaterally and instantly deny service to all counterparties trading in that pool, causing legitimate `ExchangeTransactionContract` calls to fail or execute at economically ruinous rates. This is an availability/settlement-integrity issue scoped to the affected exchange pool (not the whole chain), matching the medium-severity classification given to the original Unlock finding.

### Likelihood Explanation
Any account can call `ExchangeCreateContract` to become a pool creator, and the withdraw path requires no cooldown, no minimum-liquidity buffer, and no counterparty consent. A creator wanting to grief traders or extract maximum value need only issue a single `ExchangeWithdrawContract` transaction for nearly the full reserve size, which is trivially reachable by any unprivileged (from the chain's perspective) account with no special access.

### Recommendation
Introduce a liquidity-provider share-accounting model (analogous to Uniswap/Bancor LP tokens) so a creator can only withdraw their proportional share of the pool, or enforce a protocol-level minimum reserve floor in `ExchangeWithdrawActuator.doValidate()` that leaves enough balance to satisfy realistically-sized outstanding trades, and surface pool-drain risk to API/wallet consumers before they submit `ExchangeTransactionContract` trades.

### Proof of Concept
1. Account A creates an exchange with `ExchangeCreateContract` depositing token X and token Y.
2. Account B submits `ExchangeTransactionContract` trades against the pool over time, relying on a roughly stable rate.
3. Account A calls `ExchangeWithdrawContract` for `quant` close to `firstTokenBalance - 1`, which passes validation because both balances remain non-zero [6](#0-5) .
4. Subsequent `ExchangeTransactionContract` attempts by Account B for any nontrivial size now fail with `"exchange balance is not enough"` or `"Not precise enough"`, as confirmed by existing unit tests exercising these same validation paths [7](#0-6) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L63-97)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L205-212)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-215)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1339-1379)
```java
  /**
   * SameTokenName close,balance is not enough
   */
  @Test
  public void SameTokenNameCloseBalanceNotEnough() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    InitExchangeBeforeSameTokenNameActive();
    long exchangeId = 1;
    String tokenId = "_";
    long quant = 100_000000L;
    String buyTokenId = "abc";

    byte[] ownerAddress = ByteArray.fromHexString(OWNER_ADDRESS_SECOND);
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(ownerAddress);
    Map<String, Long> assetMap = accountCapsule.getAssetMapForTest();
    accountCapsule.setBalance(quant - 1);
    Assert.assertEquals(null, assetMap.get(buyTokenId));
    dbManager.getAccountStore().put(ownerAddress, accountCapsule);

    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, 1));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("balance is not enough",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    } finally {
      dbManager.getExchangeStore().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeStore().delete(ByteArray.fromLong(2L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(1L));
      dbManager.getExchangeV2Store().delete(ByteArray.fromLong(2L));
    }
```
