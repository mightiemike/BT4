### Title
DoS via front-run pool-balance-limit griefing in `ExchangeTransactionActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The TRC10 Bancor-style `Exchange` module enforces a global cap (`ExchangeBalanceLimit`) on the balance of either token side of an exchange pair. This cap is checked identically in `ExchangeCreateActuator`, `ExchangeInjectActuator`, and `ExchangeTransactionActuator`. Because `ExchangeTransactionContract` (trading) is callable by any unprivileged account, an attacker can front-run a victim's trade and push the target token's pool balance up to just under the limit, causing the victim's subsequent trade (which would push the balance over the limit) to revert. This mirrors the report's `_checkVolatility`/`pendingThreshold` DoS pattern: a shared threshold check on pooled state that any unprivileged transaction can push toward the boundary, causing unrelated legitimate transactions to fail.

### Finding Description
`ExchangeTransactionActuator.doValidate()` computes the post-trade balance of the traded token side and rejects the transaction if it would exceed `dynamicStore.getExchangeBalanceLimit()`: [1](#0-0) 

This same limit is enforced in `ExchangeInjectActuator`: [2](#0-1) 

and at exchange-creation time in `ExchangeCreateActuator`: [3](#0-2) 

Unlike `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, which require the caller to be the exchange creator (`accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`) [4](#0-3) , `ExchangeTransactionActuator.doValidate()` has no such restriction — any account can submit an `ExchangeTransactionContract` trade [5](#0-4) .

This creates the same class of vulnerability as the report: a malicious actor can observe a pending victim trade in the mempool that would add to one side of the pool, and front-run it with their own trade in the same direction, pushing `tokenBalance` (existing pool balance + their own quant) right up to `balanceLimit`. The victim's transaction then computes `tokenBalance = addExact(tokenBalance, tokenQuant)` against the now-higher pool balance and reverts with `"token balance must less than " + balanceLimit`. The attacker can subsequently reverse their trade (trade back in the opposite direction) to restore the pool balance and recover most of their principal, similar to the "deposit before, withdraw after" pattern described in the report for the ICHI vaults.

### Impact Explanation
This is an unprivileged, reachable transaction-level DoS: legitimate users' trades on a given `Exchange` pair can be selectively blocked by an attacker willing to temporarily park funds in the pool near the cap. Because the limit is a single global scalar shared by all participants of a given token side, and the check re-derives `tokenBalance` fresh from live exchange state on every transaction, this griefing can be repeated against any specific victim transaction as long as the attacker can front-run it (impact category: invalid-state/halt for the targeted transaction).

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to observe a pending trade transaction (mempool visibility) and successfully front-run it with a same-direction trade sized to approach `balanceLimit`, and it only affects pairs whose pool balance is already close to the configured limit (which is more likely for low/thin-liquidity or newly-capped pairs, similarly to the "low-liquidity vaults" caveat in the original report). The attack cost is bounded by the tokens the attacker must temporarily commit and any subsequent reversal trade's exchange fee/slippage, mirroring the "inherent cost" mitigation the original report notes for the ICHI case.

### Recommendation
Consider one or more of the report's original mitigations adapted to this context:
1. Re-evaluate whether the fixed `ExchangeBalanceLimit` check is strictly necessary for `ExchangeTransactionActuator` (trading), versus only for `ExchangeCreateActuator`/`ExchangeInjectActuator` (pool sizing operations), since trading a small amount pushing a near-full pool over a hard cap is more of a griefing vector than a real safety concern.
2. Alternatively, allow the limit check to be less rigid for trades near the boundary (e.g., allow trades to partially fill up to the limit rather than reverting entirely), reducing the ability of an attacker to fully block a specific victim transaction.
3. Ensure `ExchangeBalanceLimit` is set at a level, and/or is adjustable per pair based on realistic trading volume, such that legitimate trading activity is unlikely to approach the cap, reducing the attack surface for this griefing pattern.

### Proof of Concept
Conceptual reproduction based on the actual validate logic:
1. Exchange pair X/TRX has `firstTokenBalance` close to, but below, `ExchangeBalanceLimit`.
2. Victim broadcasts `ExchangeTransactionContract` trading TRX for X with quant `Q_victim` that would still keep the pool under the limit if executed alone.
3. Attacker observes the pending tx and broadcasts their own `ExchangeTransactionContract` trading TRX for X with a smaller/prioritized transaction (front-run) such that `firstTokenBalance + Q_attacker` is right at (or 1 short of) `balanceLimit` — this validates and executes successfully per [1](#0-0) .
4. Victim's transaction is processed next; `tokenBalance = addExact(firstTokenBalance, Q_victim)` now exceeds `balanceLimit`, and `doValidate()` throws `ContractValidateException("token balance must less than " + balanceLimit)`, reverting the victim's trade.
5. Attacker can submit a reverse trade (X for TRX) to bring the pool balance back down, recovering their position (minus Bancor-formula slippage/fee), and can repeat the front-run against future victim transactions.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L119-167)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L233-236)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L205-208)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```
