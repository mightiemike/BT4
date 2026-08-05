Confirmed: any account can create an exchange via `ExchangeCreateActuator` (not a privileged/trusted role), and only that creator can later call `ExchangeInject`/`ExchangeWithdraw` for that specific exchange, acting as the AMM's liquidity provider. This confirms the analog is a normal-user role, not a trusted/system role.

### Title
Unbounded slippage in `ExchangeInject`/`ExchangeWithdraw` liquidity operations lets front-running distort LP deposit/withdrawal ratios - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
java-tron's Bancor-style `Exchange` feature is the codebase's AMM analog to the reported DeFi protocol: `ExchangeCreateContract` creates a pool, `ExchangeInjectContract`/`ExchangeWithdrawContract` are the `addLiquidity()`/`removeLiquidity()` equivalents, and `ExchangeTransactionContract` is the swap equivalent. The swap path (`ExchangeTransactionContract`) already implements the exact slippage protection the external report recommends — a caller-supplied `tokenExpected` minimum. The liquidity add/remove path does not: `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `tokenId`/`quant`, with no caller-specified bound on the paired-token amount computed from the *current* pool ratio.

### Finding Description
`ExchangeInjectActuator.execute()` computes the counterpart token amount (`anotherTokenQuant`) purely from the exchange's live balances at execution time: [1](#0-0) 
This calculation depends entirely on `firstTokenBalance`/`secondTokenBalance` read at the moment the transaction executes, and the actuator's own validation only rejects a non-positive result: [2](#0-1) 

The same pattern exists in `ExchangeWithdrawActuator.execute()`, which computes `anotherTokenQuant` from the live pool ratio with no caller-supplied bound other than a "not precise enough" rounding-tolerance check, which protects against precision loss, not price movement: [3](#0-2) [4](#0-3) 

By contrast, `ExchangeTransactionContract` (the swap/trade path) *does* let the caller supply a `tokenExpected` minimum, and `ExchangeTransactionActuator.doValidate()` rejects the transaction if the computed output is below it: [5](#0-4) 
No equivalent field or check exists for `ExchangeInjectContract`/`ExchangeWithdrawContract`.

Anyone can become an exchange "creator" (the LP role) by calling `ExchangeCreateContract` — there is no privileged/committee gating: [6](#0-5) 
and only that creator may subsequently `Inject`/`Withdraw` on that specific exchange: [7](#0-6) [8](#0-7) 
So the creator is a normal, unprivileged user acting as the liquidity provider for their own pool — exactly the role described in the external report.

Because pool ratio can be moved by any third party issuing an `ExchangeTransactionContract` trade against the same exchange in an earlier transaction of the same block (or via ordinary mempool front-running), the LP's `Inject`/`Withdraw` transaction, once broadcast, can settle at a ratio very different from the one observed when the transaction was signed — spending/receiving an unpredictable, unbounded amount of the counterpart asset.

### Impact Explanation
An LP calling `ExchangeInject` to add liquidity, or `ExchangeWithdraw` to remove it, has no on-chain mechanism to bound the amount of the second token spent or received. A third party can sandwich the LP's pending transaction with `ExchangeTransactionContract` trades to shift the pool ratio, causing the LP to deposit far more of the second asset than intended (inject) or receive far less of the second asset than intended (withdraw) for the same first-token quantity. This is a concrete settlement/accounting-fairness impact on a reachable, unprivileged actuator path, directly mirroring the reported bug class, and inconsistent with the protections already built for the swap path in the same contract family.

### Likelihood Explanation
Likelihood is moderate: it requires either mempool front-running or same-block transaction ordering advantage against a specific `exchangeId`, both of which are standard, low-cost adversarial techniques available to any unprivileged network participant. The `Exchange` balances and any pending transactions targeting a given `exchangeId` are publicly observable.

### Recommendation
Add a caller-specified bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g., a maximum/minimum `anotherTokenQuant`), and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator.doValidate()`, mirroring the `tokenExpected` check already present in `ExchangeTransactionActuator`.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` from the exchange creator for `exchangeId=X`, injecting `tokenQuant` of `firstTokenId`.
2. Attacker submits an `ExchangeTransactionContract` trade against the same `exchangeId` with higher fee/priority to execute first, shifting `firstTokenBalance`/`secondTokenBalance`.
3. The LP's `ExchangeInjectActuator.execute()` recomputes `anotherTokenQuant` from the now-altered balances (lines 71-83), so the LP is charged a materially different amount of `secondTokenId` than expected when they signed the transaction — with no contract field available to cap this.
4. The same sequence applies symmetrically to `ExchangeWithdrawActuator`, causing the LP to receive less of the second token than expected on withdrawal.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
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

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L217-243)
```java
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
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
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
