Based on my analysis, the strongest analog to this report exists in java-tron's TRC10 token AMM ("Exchange") feature, but it's split between two contract types with different protections:

**`ExchangeTransactionContract`** (the actual swap function) already implements the exact fix the report recommends — an `expected` field checked in validation (`anotherTokenQuant < tokenExpected` fails) [1](#0-0) . So this path is not vulnerable.

**`ExchangeInjectContract`/`ExchangeWithdrawContract`**, however, compute the paired-token amount purely from the *current* pool ratio at execution time, with **no minimum/maximum bound supplied by the caller** [2](#0-1) [3](#0-2) . The protobuf definitions confirm this asymmetry — `ExchangeTransactionContract` has an `expected` field while `ExchangeInjectContract`/`ExchangeWithdrawContract` do not [4](#0-3) .

### Title
Sandwich attack on TRC10 AMM liquidity inject/withdraw due to missing slippage protection - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeInjectActuator.execute()` and `ExchangeWithdrawActuator.execute()` calculate the counterpart token amount from the pool's *current* balances at the moment the transaction executes, with no caller-supplied minimum/maximum bound, mirroring the reported `Omnipool.swapForGem()` bug class of accepting "any exchange rate."

### Finding Description
Both actuators derive `anotherTokenQuant` directly from the live `firstTokenBalance`/`secondTokenBalance` ratio of the `ExchangeCapsule` [2](#0-1) [5](#0-4) . Neither the contract message nor `doValidate()` accepts a minimum-out / maximum-in bound for this computed value, unlike `ExchangeTransactionContract` which explicitly has `expected` for this purpose [6](#0-5) . Any unprivileged, anonymous account can broadcast `ExchangeTransactionContract` swaps immediately before and after a pending inject/withdraw transaction observed in the mempool to skew the pool ratio, forcing the inject/withdraw to execute against a manipulated ratio, then reverse the skew and pocket the difference — a classic AMM sandwich attack, matching the report's root cause of "blindly" trusting current pool state without slippage protection.

### Impact Explanation
An attacker can extract value from whoever injects or withdraws liquidity on a TRC10 exchange pool by sandwiching the transaction with `ExchangeTransactionContract` swaps, corrupting the accounting of tokens received/given, similar in kind to the reward-theft mechanism described in the report.

### Likelihood Explanation
Exploitation only requires observing a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction in the network and being fast enough to submit surrounding swaps — both actions are available to any unprivileged account via `wallet/exchangeinject`, `wallet/exchangewithdraw`, and `wallet/exchangetransaction` HTTP/gRPC endpoints [7](#0-6) . Note, however, that only the pool's `creatorAddress` can call inject/withdraw [8](#0-7) [9](#0-8) , so the victim population is limited to exchange creators rather than arbitrary users, which reduces overall exploitability relative to the original report's broad "any user claiming rewards" scenario.

### Recommendation
Add an `expected`/bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to the existing `ExchangeTransactionContract.expected`), and validate the calculated `anotherTokenQuant` against it in `ExchangeInjectActuator.doValidate()`/`ExchangeWithdrawActuator.doValidate()` before executing, so callers can enforce a slippage tolerance.

### Proof of Concept
1. Exchange creator broadcasts `ExchangeInjectContract` intending to add liquidity at the pool's current ratio.
2. An unprivileged attacker observes this pending transaction and front-runs it with an `ExchangeTransactionContract` swap that skews `firstTokenBalance`/`secondTokenBalance`.
3. The creator's inject executes against the skewed ratio in `ExchangeInjectActuator.execute()` [2](#0-1) , causing the creator to deposit a disadvantageous ratio of tokens.
4. The attacker back-runs with a reverse swap to restore the ratio and capture the difference extracted from the creator's injected liquidity.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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

**File:** Tron protobuf protocol document.md (L1394-1420)
```markdown
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

**File:** Tron protobuf protocol document.md (L1422-1440)
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
```

**File:** framework/src/test/java/org/tron/common/utils/client/utils/HttpMethed.java (L498-522)
```java
  public static HttpResponse exchangeInject(
      String httpNode,
      byte[] ownerAddress,
      Integer exchangeId,
      String tokenId,
      Long quant,
      String fromKey) {
    try {
      final String requestUrl = "http://" + httpNode + "/wallet/exchangeinject";
      JsonObject userBaseObj2 = new JsonObject();
      userBaseObj2.addProperty("owner_address", ByteArray.toHexString(ownerAddress));
      userBaseObj2.addProperty("exchange_id", exchangeId);
      userBaseObj2.addProperty("token_id", str2hex(tokenId));
      userBaseObj2.addProperty("quant", quant);
      response = createConnect(requestUrl, userBaseObj2);
      transactionString = EntityUtils.toString(response.getEntity());
      transactionSignString = gettransactionsign(httpNode, transactionString, fromKey);
      response = broadcastTransaction(httpNode, transactionSignString);
    } catch (Exception e) {
      e.printStackTrace();
      httppost.releaseConnection();
      return null;
    }
    return response;
  }
```
