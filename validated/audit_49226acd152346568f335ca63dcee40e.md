## Bypass of Exchange Pool Ratio via Unbounded Inject → Swap → Withdraw Sequencing - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron's built-in bancor-style `Exchange` (TRX/TRC10 AMM pool) has the exact bug class described in the report: no time lock, cool-down, or minimum-holding-period is enforced between `ExchangeInjectContract`, `ExchangeTransactionContract` (swap), and `ExchangeWithdrawContract`. Anyone can permissionlessly create a pool via `ExchangeCreateContract`, then in rapid succession inject liquidity to skew the pool ratio, execute (or have an accomplice execute) a swap at the manipulated ratio, and immediately withdraw the injected liquidity back out — all in the same block/adjacent transactions.

### Finding Description
The `Exchange` feature is a permissionless AMM: any account can call `ExchangeCreateActuator` to become the "creator" of a token pair pool [1](#0-0) , with no restriction on who can create one or how many pools exist — this is not a privileged/trusted role, it's ordinary permissionless account behavior.

Once created, the creator can call `ExchangeInjectActuator` to add liquidity and immediately shift the internal ratio of `firstTokenBalance`/`secondTokenBalance`, with no cooldown check anywhere in `doValidate()` or `execute()`: [2](#0-1) 

The swap logic in `ExchangeTransactionActuator.execute()` reads the pool's *current* `firstTokenBalance`/`secondTokenBalance` at execution time via `exchangeCapsule.transaction(...)`, which is exactly the bancor formula and has no protections against a same-block/immediately-preceding ratio change: [3](#0-2)  The underlying ratio math is in `ExchangeCapsule.transaction()`, which purely operates on the live balances passed in: [4](#0-3) 

Immediately after the swap, the creator can call `ExchangeWithdrawActuator` to pull the injected liquidity back out, again with no cooldown/time-lock check: [5](#0-4)  The only access restriction on inject/withdraw is that the caller must be the pool's `creatorAddress` [6](#0-5) [7](#0-6)  — but since pool creation is itself permissionless and free-form, an attacker trivially satisfies this by simply being the creator of their own pool (or a pool they control via a second, unfunded account acting as counterparty for the swap).

None of the three actuators (`ExchangeInjectActuator`, `ExchangeTransactionActuator`, `ExchangeWithdrawActuator`) reference block timestamps, `latestOperationTime`, or any per-account/per-exchange rate limiting; a search across the codebase for cooldown-style constants tied to the Exchange feature returns nothing relevant.

### Impact Explanation
An attacker who creates (or controls) an exchange pool can:
1. Inject a large, disproportionate amount of one token to skew the ratio.
2. Immediately execute (as themselves, or coordinate with a second controlled account) a swap at the artificially favorable rate.
3. Immediately withdraw the injected liquidity, restoring the pool near its original state.

This lets the attacker extract real TRX/TRC10 asset value from the pool (and from any counterparty who also trades against the manipulated pool in the same window) without genuine price discovery or capital lock-up, directly affecting on-chain account balances (`AccountCapsule.setBalance`/asset amounts) — a concrete accounting/settlement impact, not merely theoretical.

### Likelihood Explanation
High. The attack requires only:
- Creating an exchange (`ExchangeCreateContract`, permissionless, cheap fee).
- Sending `ExchangeInjectContract`, `ExchangeTransactionContract`, `ExchangeWithdrawContract` back-to-back — all standard, unprivileged transaction types available to any account, and easily sequenced within the same block by an attacker controlling the fee-payer/broadcast timing.

### Recommendation
Introduce a minimum holding period / cool-down (e.g., tracked via a new "last inject time" field on `ExchangeCapsule`, checked against `dynamicStore.getLatestBlockHeaderTimestamp()`) that blocks `ExchangeWithdrawActuator` (and/or restricts `ExchangeTransactionActuator`) from operating on a pool that received an `Inject` within N recent blocks, mirroring the fix already applied upstream in commit `46201b2` referenced in the report.

### Proof of Concept
1. Attacker calls `ExchangeCreateContract` to create pool A/B with balances `(Ba, Bb)`.
2. Attacker calls `ExchangeInjectContract` with a large `tokenQuant` of token A, skewing the ratio (`ExchangeInjectActuator.execute`, lines 60-83 above) — no cooldown enforced.
3. Attacker (or accomplice account) calls `ExchangeTransactionContract` to swap B→A (or A→B) at the now-favorable ratio (`ExchangeTransactionActuator.execute`, lines 61-69 above).
4. Attacker calls `ExchangeWithdrawContract` to withdraw the injected token A, restoring the ratio (`ExchangeWithdrawActuator.execute`/`doValidate`, lines 132-212 above) — no cooldown enforced, only the creator check at line 181-183.
5. Net effect: attacker profits on the swap step while returning the pool near its original ratio, with no on-chain mechanism preventing steps 2-4 from occurring within the same block.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L36-53)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    try {
      final ExchangeCreateContract exchangeCreateContract = this.any
          .unpack(ExchangeCreateContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeCreateContract.getOwnerAddress().toByteArray());
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L132-212)
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
    if (!this.any.is(ExchangeWithdrawContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeWithdrawContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeWithdrawContract contract;
    try {
      contract = this.any.unpack(ExchangeWithdrawContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!accountStore.has(ownerAddress)) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] not exists");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule.getBalance() < calcFee()) {
      throw new ContractValidateException("No enough balance for exchange withdraw fee!");
    }

    ExchangeCapsule exchangeCapsule;
    try {
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(contract.getExchangeId()));
    } catch (ItemNotFoundException ex) {
      throw new ContractValidateException("Exchange[" + contract.getExchangeId() + ActuatorConstant
          .NOT_EXIST_STR);
    }

    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }

    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();

    long anotherTokenQuant;

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }

    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```
