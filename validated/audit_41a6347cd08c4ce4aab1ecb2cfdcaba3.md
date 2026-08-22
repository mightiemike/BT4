## Title
`ExchangeTransactionActuator` charges zero protocol fee on TRX↔TRC10 AMM swaps while all other Exchange operations do — swap value flows only to the pool creator with no protocol-level cut - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java])

## Summary
java-tron implements an on-chain Bancor-style constant-product-like AMM ("Exchange") for trading TRX against TRC10 tokens, exposed via `ExchangeCreateContract`, `ExchangeInjectContract`, `ExchangeWithdrawContract`, and `ExchangeTransactionContract`. `ExchangeCreateActuator`, `ExchangeInjectActuator`, and `ExchangeWithdrawActuator` all charge a nonzero, chain-configured fee (`calcFee()`, e.g. `EXCHANGE_CREATE_FEE`) that is sent to the black-hole/burn sink. The actual swap actuator, `ExchangeTransactionActuator`, overrides `calcFee()` to unconditionally return `0`: [1](#0-0) 

meaning that every trade executed against the bonding-curve pool via `exchangeCapsule.transaction(...)` pays no protocol/creator fee at all: [2](#0-1) [3](#0-2) 

## Finding Description
This is structurally analogous to the SushiTrident `IndexPool` finding: several actuators that touch the pool's liquidity (create/inject/withdraw) collect a fee, but the actuator that performs the value-generating trade does not skim anything to the protocol/pool owner. In java-tron's Exchange system:

- `ExchangeCreateActuator.calcFee()`, `ExchangeInjectActuator.calcFee()`, `ExchangeWithdrawActuator.calcFee()` each return a nonzero fee (e.g. tied to `EXCHANGE_CREATE_FEE`), taken from the owner's TRX balance in addition to the token amounts moved.
- `ExchangeTransactionActuator.calcFee()` returns `0` — confirmed by the annotated override at end of the file.
- `ExchangeCapsule.transaction()` (the swap math) purely computes the bancor/constant-product output via `ExchangeProcessor`/`SafeExchangeProcessor`, with no fee percentage subtracted from `buyTokenQuant` before updating pool balances.

Because there is no trading fee (basis-point cut) taken on the actual swap, the exchange's creator (who deposited/injected the initial liquidity and functions like the sole "LP") earns no return from trading volume — the entire arbitrage/trading activity is a zero-sum transfer between the trader and the pool with no incentive skimmed back to the creator or to a protocol treasury. This is exactly the missing "bar fee" pattern described in the report: fee is charged on liquidity-management operations but not on the operation that generates ongoing trading value.

## Impact Explanation
This does not directly cause fund theft or consensus divergence, but it constitutes an accounting/incentive-design gap reachable by any broadcast `ExchangeTransactionContract` transaction:
- Exchange creators get no compensation for providing liquidity to arbitrageurs who repeatedly rebalance the pool against external markets, unlike `ExchangeCreateActuator`/`ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which do charge TRX fees for managing that same liquidity.
- This can rationally disincentivize use of java-tron's built-in Exchange (in favor of external fee-charging AMMs), and any liquidity that remains is economically exposed to fee-free extraction by arbitrage bots, degrading the value of the pool over time with no revenue accrual to anyone.

I could not find evidence that this is a newly introduced bug rather than TRON's original intended design (the Exchange was documented from inception as a free Bancor-relay, not a fee-earning AMM), so likelihood of it being an unintentional regression is uncertain.

## Likelihood Explanation
High reachability: any account can submit an `ExchangeTransactionContract` transaction and trigger the fee-free code path on every trade, with no privileged access required. However, whether this is a genuine vulnerability (as opposed to intended zero-fee design carried over from the original TRON Exchange design, later augmented by the separate TRC10 order-book "Market" which does charge fees per `MarketSellAssetActuator`/`MarketCancelOrderActuator`) is uncertain and could not be conclusively confirmed from the code alone.

## Recommendation
If a trading fee is intended for the Exchange market (as is standard for AMMs and analogous to `MARKET_SELL_FEE`/`MARKET_CANCEL_FEE` used by the newer order-book `Market` actuators), add a configurable basis-point fee inside `ExchangeCapsule.transaction()` (or in `ExchangeTransactionActuator.execute()`), skimming a portion of `buyTokenQuant` to the black-hole/burn sink or to the exchange creator, mirroring the fee model already used by `ExchangeCreateActuator`/`ExchangeInjectActuator`/`ExchangeWithdrawActuator`.

## Proof of Concept
1. Governance/creator calls `ExchangeCreateActuator` to create a TRX/TRC10 pool, paying `EXCHANGE_CREATE_FEE`.
2. Any account broadcasts repeated `ExchangeTransactionContract` transactions swapping back and forth between the two tokens.
3. Because `ExchangeTransactionActuator.calcFee()` returns `0` and `ExchangeCapsule.transaction()` applies no fee cut, each swap only changes pool balances per the bonding-curve formula — no value accrues to the pool creator or a protocol fee sink, unlike `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which always deduct `calcFee()` from the account balance for the same pool.
4. Repeated arbitrage against external price feeds can drain relative value from the pool over time while the creator earns nothing, contrasting with fee-bearing liquidity operations on the same pool. [4](#0-3) [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L45-99)
```java
    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    try {
      final ExchangeTransactionContract exchangeTransactionContract = this.any
          .unpack(ExchangeTransactionContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeTransactionContract.getOwnerAddress().toByteArray());

      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L119-224)
```java
  private boolean doValidate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    if (!this.any.is(ExchangeTransactionContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeTransactionContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeTransactionContract contract;
    try {
      contract = this.any.unpack(ExchangeTransactionContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + NOT_EXIST_STR);
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule.getBalance() < calcFee()) {
      throw new ContractValidateException("No enough balance for exchange transaction fee!");
    }

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(contract.getExchangeId()));
    } catch (ItemNotFoundException ex) {
      throw new ContractValidateException("Exchange[" + contract.getExchangeId()
          + ActuatorConstant.NOT_EXIST_STR);
    }

    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L232-235)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L85-99)
```java
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
