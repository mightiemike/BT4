### Title
Napier-style fee/price front-running analog in Napier TRC10 Exchange — pool creator can inject/withdraw tokens to manipulate exchange rate around victim swaps - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Anyone can permissionlessly create a Bancor-style TRC10 `Exchange` pool via `ExchangeCreateActuator` (no special role check — only balance/asset checks in `doValidate()`). Once created, the pool creator is free to call `ExchangeInjectContract`/`ExchangeWithdrawContract` at any time, with no rate limit, cooldown, or timelock, to instantly change `firstTokenBalance`/`secondTokenBalance` of the pool, which directly determines the bonding-curve exchange rate used by `ExchangeCapsule.transaction()`. This mirrors the Napier `poolOwner` fee-front-running bug class: a party who controls a pricing parameter can shift it immediately before a victim's swap executes and revert it right after, extracting value from unsuspecting traders. Unlike `UpdateBrokerageContract` (restricted to registered witnesses — a trusted SR role) or committee/witness-controlled `Proposal` parameters, exchange creation and inject/withdraw calls require no privileged role — any regular account is both creator and "attacker" here, satisfying the unprivileged-user analog requirement.

### Finding Description
`ExchangeCreateActuator.execute()` lets any account with sufficient balance create a two-token AMM pool, storing initial balances in `ExchangeCapsule` (`actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java:36-134`). The creator address is recorded as `creatorAddress`, and only the creator is authorized to call `ExchangeInjectActuator`/`ExchangeWithdrawActuator` (checked via `accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`, see `ExchangeInjectActuator.java:175-177` and `ExchangeWithdrawActuator.java:181-183`). These actuators directly mutate `firstTokenBalance`/`secondTokenBalance` (`ExchangeInjectActuator.java:71-83`, `ExchangeWithdrawActuator.java:77-89`), which are exactly the inputs to the constant-product-like formula in `ExchangeCapsule.transaction()` (`chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java:124-168`) and `ExchangeProcessor.exchange()` (`chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java:41-45`).

There is no delay, cooldown, or timelock on inject/withdraw operations — the creator can call them in any block, including the block immediately preceding a victim's `ExchangeTransactionContract`. A victim's only protection is the `expected` field checked in `ExchangeTransactionActuator.doValidate()` (`actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java:217-221`), which — exactly as noted in the Sherlock discussion for the Napier report — is a slippage floor, not a guarantee against malicious short-term price manipulation. A creator can inject tokens to skew the rate unfavorably for the victim (still passing the victim's slippage tolerance if it's not maximally tight) and withdraw immediately after the victim's swap to restore/rebalance the pool and capture the difference, or repeatedly widen/narrow the pool ratio to always execute victim swaps at the worst rate within their tolerance.

### Impact Explanation
This allows a permissionless pool creator to systematically extract value from ordinary swap users by manipulating pool ratios around their trades — an accounting/market-manipulation impact identical in class to the Napier issue (unfair fee/price extraction via front-running), but here achieved by manipulating AMM reserves rather than a fee parameter. It degrades trust in the TRC10 exchange feature and directly costs unprivileged swappers value on every affected trade, since the underlying vulnerability class was accepted (as Medium) in the original Napier finding for lacking any delay mechanism on manipulable pricing parameters.

### Likelihood Explanation
Likelihood is high in practice: creating an exchange, injecting/withdrawing liquidity, and executing a transaction are all ordinary, unprivileged, un-throttled operations available on-chain to any account with sufficient balance and reachable in the same or immediately adjacent transactions/blocks by the pool creator, mirroring the "front-run swap, revert after" pattern described in the original report's PoC.

### Recommendation
Introduce a minimum delay/cooldown (e.g., a block-count or timestamp-based lock) between successive `ExchangeInjectContract`/`ExchangeWithdrawContract` calls on the same exchange, or between an inject/withdraw and a subsequent swap within the same block, similar to the recommended fix for Napier's `setFeeParameter`. Additionally, consider disallowing inject/withdraw and swap operations for the same exchange within the same block to prevent same-block manipulation, and strengthen the `expected` slippage check with an explicit maximum allowed price-impact bound tied to the pool's actual liquidity depth rather than only relying on a fixed `expected` output.

### Proof of Concept
1. Attacker (any unprivileged account) calls `ExchangeCreateContract` to create a `TRX/TokenX` pool with balances `[A, B]` (`ExchangeCreateActuator.java:36-134`).
2. Victim broadcasts a `ExchangeTransactionContract` swap with `expected` set to a modest slippage tolerance.
3. Attacker, seeing the pending swap (mempool), submits `ExchangeWithdrawContract`/`ExchangeInjectContract` in the same or an earlier block to shift the pool ratio unfavorably for the victim while still keeping the victim's output above `tokenExpected` (`ExchangeTransactionActuator.java:217-221`), because `expected` only enforces a floor, not the fair rate.
4. Victim's swap executes at the worse-than-fair rate via `ExchangeCapsule.transaction()` (`chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java:124-168`).
5. Attacker calls `ExchangeInjectContract`/`ExchangeWithdrawContract` again to restore the pool to its original ratio, capturing the difference extracted from the victim's swap, with no protocol-level delay preventing this repeated cycle. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L145-182)
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
    if (!this.any.is(ExchangeCreateContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeCreateContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeCreateContract contract;
    try {
      contract = this.any.unpack(ExchangeCreateContract.class);
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
      throw new ContractValidateException("No enough balance for exchange create fee!");
    }

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-89)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-168)
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
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```
