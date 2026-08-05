### Title
Unprivileged trade front-running can push a Bancor exchange's token balance to `EXCHANGE_BALANCE_LIMIT`, denying other users' trades - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
`ExchangeTransactionActuator` (java-tron's Bancor-style TRC10 exchange trade actuator) validates that a trade would not push a token's exchange balance above the global `exchangeBalanceLimit`. Because this check reads the *current* mutable balance of the shared `ExchangeCapsule` at validation time, any unprivileged user can front-run another user's trade with a trade that pushes the balance close to the limit, causing the victim's `ExchangeTransactionContract` to fail validation. This is a direct structural analog of the reported `maxContractBalance` deposit-denial bug: a global hard cap checked against a shared, attacker-influenceable balance, enabling transaction ordering griefing.

### Finding Description
In `doValidate()`, the actuator reads the exchange's current `firstTokenBalance`/`secondTokenBalance` from the shared, on-chain `ExchangeCapsule`, adds the caller's trade quantity, and rejects if it exceeds `dynamicStore.getExchangeBalanceLimit()`: [1](#0-0) 

This check is against global, shared state (`ExchangeCapsule` balances) that any account can influence, since `ExchangeTransactionContract` is not creator-restricted — unlike `ExchangeInjectActuator`, which is restricted to the exchange creator: [2](#0-1) 

`ExchangeTransactionActuator` has no such creator restriction — any account holding the traded token can call it, and it recomputes the post-trade balance from live state before comparing to the fixed limit: [3](#0-2) 

The same pattern (hard balance cap checked against attacker-movable shared state) also exists at exchange creation and injection, but those either create a new isolated exchange or are creator-gated, making `ExchangeTransactionActuator` the only path reachable by arbitrary unprivileged users analogous to the original "any user can deposit" report: [4](#0-3) 

### Impact Explanation
An attacker (or a block producer / same-block transaction orderer, as noted in the original report) can submit a large trade transaction that pushes one side of the exchange's token balance to just under `EXCHANGE_BALANCE_LIMIT`. Any subsequent trade transaction in the same pool of exchange, submitted by a legitimate user in the same or later block, whose resulting balance would cross that limit is rejected with `"token balance must less than " + balanceLimit`, causing `ContractValidateException` and transaction failure (fee still lost since `calcFee()` is charged on execute, though validate failures don't consume fee in most actuators — this is state/accounting griefing, not fund theft). This lets an attacker manipulate a specific TRC10/TRX exchange market to selectively block competitors from trading on it, mirroring the "renting cards" market-manipulation scenario in the original report (denial-of-service on a specific market participant, invalid-state/halt category).

### Likelihood Explanation
Exploitability requires the attacker to have enough of the relevant token/TRX to push the balance toward the limit and to control transaction ordering (front-running via fee/miner cooperation, or same-block ordering), which is a known and practical capability in blockchain systems as the original report itself argues. The check is purely a function of on-chain state readable by anyone (`ExchangeCapsule` balances are public), making the precondition for the attack fully computable in advance.

### Recommendation
Reconsider the purpose of a fixed global `exchangeBalanceLimit` on a per-exchange basis: either remove it, make it per-exchange, or ensure it cannot be used to grief specific transactions (e.g., partial-fill semantics instead of all-or-nothing rejection, or graceful degradation instead of hard revert when a trade would exceed the limit).

### Proof of Concept
1. Exchange E has `firstTokenBalance = X`, and `EXCHANGE_BALANCE_LIMIT = L`, with `L - X` small.
2. Victim broadcasts `ExchangeTransactionContract` trading `tokenQuant` such that `X + tokenQuant <= L` (expected to succeed).
3. Attacker observes the victim's pending transaction and front-runs with their own `ExchangeTransactionContract` trading `tokenQuant' `such that `X + tokenQuant' `is just below `L` but `X + tokenQuant' + tokenQuant > L`.
4. Attacker's transaction executes first (via fee bump or same-block ordering), updating `exchangeCapsule` balance via [5](#0-4) .
5. Victim's transaction is then validated against the now-updated balance and fails the check at [1](#0-0) , throwing `ContractValidateException("token balance must less than " + balanceLimit)`, denying the victim's trade.

Note: I was unable to fully verify the default numeric value and admin-configurability constraints of `EXCHANGE_BALANCE_LIMIT` in `DynamicPropertiesStore.java` within the available context (only match locations were found, not the surrounding getter/setter/default-value code), so the exact default cap size is unconfirmed.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L57-93)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L119-156)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L205-208)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```
