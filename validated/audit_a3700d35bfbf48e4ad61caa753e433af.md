Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` protobuf messages carry only `owner_address`, `exchange_id`, `token_id`, and `quant` — no minimum/maximum bound field — and neither `ExchangeInjectActuator.doValidate()` nor `ExchangeWithdrawActuator.doValidate()` accept or check any caller-supplied expected/limit value against the computed `anotherTokenQuant`, unlike `ExchangeTransactionActuator`, which validates `anotherTokenQuant < tokenExpected` at [1](#0-0) .

### Title
Missing Slippage/Bound Protection on `ExchangeInject`/`ExchangeWithdraw` Enables Sandwich Manipulation of AMM Pool Ratio - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
java-tron's built-in Bancor-style AMM ("Exchange") exposes three unprivileged, broadcast-reachable operations: swap (`ExchangeTransactionContract`), add liquidity (`ExchangeInjectContract`), and remove liquidity (`ExchangeWithdrawContract`). Only the swap operation implements slippage protection via the `expected` field, which is validated against the computed output before execution. The inject and withdraw operations compute their counterpart-token amount (`anotherTokenQuant`) purely from the pool's live balances *at execution time*, with no user-supplied bound and no contract field to express one, exactly mirroring the reported bug class where "decrease/close" trades lack the slippage protection that "open/increase" trades have.

### Finding Description
`ExchangeTransactionContract` includes an `expected` field [2](#0-1) , documented as "expected minimum number of tokens" in the protocol spec [3](#0-2) , and `ExchangeTransactionActuator.doValidate()` enforces it: `anotherTokenQuant` (computed from the current pool state) must not be less than `tokenExpected`, or the transaction is rejected before any state mutation [1](#0-0) .

In contrast, `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no field to express a minimum acceptable counterpart amount [4](#0-3) .

`ExchangeInjectActuator.execute()` computes `anotherTokenQuant` directly from `firstTokenBalance`/`secondTokenBalance` read at execution time and immediately debits the account and updates the pool with no user-controlled bound check: [5](#0-4) . Its `doValidate()` performs address/token/balance sanity checks but never compares `anotherTokenQuant` to any caller-supplied bound [6](#0-5) .

Likewise, `ExchangeWithdrawActuator.execute()` computes `anotherTokenQuant` the same way and credits the account/pool without any minimum-received check [7](#0-6) ; its `doValidate()` only checks a "precision loss" tolerance against the pool's *current* ratio, not against any value the withdrawer specified when building the transaction [8](#0-7) .

Because the pool ratio (`firstTokenBalance`/`secondTokenBalance`) can be shifted by any other account submitting an `ExchangeTransactionContract` (swap) in an earlier position within the same block or a preceding block — a standard, permissionless, no-special-privilege action — an attacker can sandwich a victim's pending `ExchangeInject`/`ExchangeWithdraw` transaction: swap to skew the ratio immediately before the victim's transaction executes, let the victim contribute/withdraw at the skewed ratio, then reverse the swap afterward. This is the same root cause as the GMX report: an operation that mutates a position/pool at a price/ratio determined only at execution time, with no way for the caller to bound the acceptable outcome.

### Impact Explanation
A victim injecting liquidity can be forced to contribute tokens at a manipulated ratio, effectively donating value to the attacker's LP share or losing tokens relative to fair market ratio. A victim withdrawing liquidity can be forced to receive a disadvantageous split of the two pooled tokens. Because `Commons.putExchangeCapsule` persists the mutated pool state and `accountStore.put` persists the new balances unconditionally once validation passes [9](#0-8) , the loss is realized on-chain with no recourse. This is a direct funds-loss/asset-accounting-corruption impact, consistent with a Medium-severity classification analogous to the source report.

### Likelihood Explanation
Any account can broadcast `ExchangeInjectContract`/`ExchangeWithdrawContract` transactions; no privileged role, leaked key, or malicious node/peer behavior is required — only the ability to observe the mempool/broadcast a competing swap transaction, which is standard, permissionless blockchain behavior. Given java-tron's transaction ordering is determined by witnesses packing blocks, an attacker (including a witness itself, but the exploit does not require witness privilege — normal accounts racing/front-running transactions suffice) can reliably position swap transactions around a target inject/withdraw transaction.

### Recommendation
Add a caller-specified bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g., `expected_another_token_min` / `expected_another_token_max` as appropriate), and enforce it in `ExchangeInjectActuator.doValidate()` / `ExchangeWithdrawActuator.doValidate()` before mutating state, the same way `ExchangeTransactionActuator` already enforces `expected` against `anotherTokenQuant`.

### Proof of Concept
1. Pool state: `firstTokenBalance = 100_000_000`, `secondTokenBalance = 200_000_000` (ratio 1:2).
2. Victim broadcasts `ExchangeInjectContract{token_id=first, quant=1_000_000}`, expecting `anotherTokenQuant ≈ 2_000_000` based on ratio observed at submission time.
3. Attacker broadcasts an `ExchangeTransactionContract` swap that sells a large amount of the second token into the pool just before the victim's transaction is packed, shifting the ratio (e.g., to 1:1).
4. Victim's `ExchangeInjectActuator.execute()` recomputes `anotherTokenQuant` from the now-skewed live balances [10](#0-9)  — since there is no `doValidate()` check comparing this against any victim-specified minimum, the injection proceeds at the unfavorable ratio, e.g., `anotherTokenQuant ≈ 1_000_000` instead of the expected `2_000_000`.
5. Attacker reverses the swap afterward, restoring the ratio and pocketing the difference contributed by the victim.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** protocol/src/main/protos/core/Tron.proto (L1-1)
```text
syntax = "proto3";
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

**File:** Tron protobuf protocol document.md (L1422-1442)
```markdown
     - message `ExchangeTransactionContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to sell.
    
       `quant`: token amount to sell.
    
       `expected`: expected minimum number of tokens.
    
      ```java
      message ExchangeTransactionContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
          int64 expected = 5;
      }
      ```
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L68-83)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L100-106)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeInjectAnotherAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L125-154)
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

    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-240)
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
```
