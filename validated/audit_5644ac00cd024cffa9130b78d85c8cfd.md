Confirmed: `ExchangeCreateActuator` is unprivileged — any account with enough balance to pay the `ExchangeCreateFee` can create a bancor-style pool [1](#0-0) , and any token holder can subsequently call `ExchangeInjectContract`/`ExchangeWithdrawContract` against it without any privileged role check [2](#0-1) . This confirms the analog is reachable by ordinary users, matching the "unprivileged-user" requirement.

### Title
Lack of slippage protection in `ExchangeInjectContract`/`ExchangeWithdrawContract` liquidity operations enables sandwich attacks - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Unlike `ExchangeTransactionContract`, which carries a user-supplied `expected` minimum-return field enforced in `ExchangeTransactionActuator` [3](#0-2) , the `ExchangeInjectContract` and `ExchangeWithdrawContract` messages have no equivalent min/max bound field at all [4](#0-3) . Both actuators compute the counter-token amount (`anotherTokenQuant`) purely from the exchange pool's *live* balance ratio at execution time, with no user-provided tolerance to cap what they are willing to pay or receive.

### Finding Description
In `ExchangeInjectActuator.execute`/`doValidate`, when a user injects `tokenQuant` of one token, the required amount of the paired token is derived on-the-fly from the current pool ratio:
```
anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance
``` [5](#0-4)  and the caller has no way to cap `anotherTokenQuant`. Similarly, `ExchangeWithdrawActuator.doValidate` computes the amount of the paired token returned to the withdrawer from the same live ratio [6](#0-5) , again with no user-supplied minimum acceptable amount.

Because the exchange pool ratio can be shifted arbitrarily within the same block by any other account submitting an `ExchangeTransactionContract` trade against the same pool immediately before the victim's inject/withdraw transaction (miners/packers order transactions and there is no atomicity/ordering guarantee tying the victim's inject/withdraw to a specific expected ratio), this is a textbook sandwich-attack setup: an attacker can trade against the pool to distort the ratio, let the victim's inject execute (forcing the victim to contribute far more of the second token than fair value) or withdraw execute (returning far less of the second token than fair value), and then reverse the initial trade to restore the ratio and pocket the difference. This is the exact bug class described in the external report — mint/liquidity-provision functions that omit `amountMin`/`amountMax` slippage bounds, exposing users to price manipulation during liquidity provision/removal.

### Impact Explanation
A victim calling `ExchangeInjectContract` can be forced to deposit substantially more of the counter-asset than the fair-value ratio at the time they submitted the transaction, with the surplus effectively captured by the attacker who manipulated the ratio. A victim calling `ExchangeWithdrawContract` can receive substantially less of the counter-asset than fair value for the same reason. This is a direct accounting/value-loss impact on unprivileged users interacting with the on-chain bancor-style `Exchange`/`ExchangeV2` market feature, analogous to funds being effectively swept away via price manipulation, as described in the report (impact category: underpriced/mispriced settlement due to missing slippage bound).

### Likelihood Explanation
Likelihood is bounded by the fact that TRON block-packing/transaction ordering is controlled by the witness producing the block, and while ordinary transaction submission does not guarantee an attacker can control ordering relative to a specific victim transaction within the same block, an attacker (especially one with witness/packing influence, or simply racing transactions across adjacent blocks since price impact persists until reversed) can still realistically manipulate the ratio shortly before a victim's inject/withdraw executes, particularly for lower-liquidity exchange pairs where price impact per trade is significant. This mirrors exactly the acknowledged (low-risk) classification in the original report for the analogous AMM functions.

### Recommendation
Add optional minimum/maximum bound fields to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to the existing `expected` field on `ExchangeTransactionContract`), and enforce them in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` doValidate, rejecting the transaction if the computed `anotherTokenQuant` falls outside the user-specified tolerance.

### Proof of Concept
1. Pool `P` holds `firstToken`/`secondToken` at ratio `R0`.
2. Victim broadcasts `ExchangeInjectContract` intending to inject `X` of `firstToken`, expecting to also contribute `Y0 = secondBalance*X/firstBalance` of `secondToken` at ratio `R0`.
3. Attacker submits an `ExchangeTransactionContract` trade against pool `P` that lands before the victim's transaction in the same block (or in an immediately preceding block), shifting the ratio to `R1` such that `secondBalance` is inflated relative to `firstBalance`.
4. Victim's `ExchangeInjectActuator` executes using `R1`, computing `anotherTokenQuant = Y1 > Y0`, forcing the victim to pay more `secondToken` than intended [7](#0-6) .
5. Attacker submits a reverse `ExchangeTransactionContract` trade restoring the ratio to near `R0`, capturing the price-impact profit while the victim is left having over-contributed `secondToken` to the pool with no on-chain mechanism (`expected`/min/max field) to have prevented or bounded this loss.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L145-181)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L125-146)
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
    if (!this.any.is(ExchangeInjectContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [ExchangeInjectContract],real type[" + any
              .getClass() + "]");
    }
    final ExchangeInjectContract contract;
    try {
      contract = this.any.unpack(ExchangeInjectContract.class);
    } catch (InvalidProtocolBufferException e) {
      throw new ContractValidateException(e.getMessage());
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** Tron protobuf protocol document.md (L1384-1420)
```markdown
     - message `ExchangeInjectContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to inject.
    
       `quant`: token amount to inject.
    
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
    
     - message `ExchangeWithdrawContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to withdraw.
    
       `quant`: token amount to withdraw.
    
      ```java
      message ExchangeWithdrawContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-227)
```java
    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
```
