## Title
Exchange pool funds become permanently unwithdrawable once trading drains either reserve to zero - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The `Exchange` feature in java-tron (TRC10 AMM-like pools created via `ExchangeCreateContract`, traded via `ExchangeTransactionContract`) allows any account to permissionlessly trade against a pool's bonded-curve reserves, and lets the exchange creator recover their deposited liquidity via `ExchangeWithdrawContract`. Normal trading can legitimately drive one side of the reserve pair to exactly zero. Once that happens, the only function capable of returning funds to the creator - `ExchangeWithdrawActuator` - unconditionally reverts because it checks `firstTokenBalance == 0 || secondTokenBalance == 0` and treats this as "the exchange has been closed." There is no other actuator or recovery path to reclaim the remaining (non-zero) side of the pool, so the creator's residual funds are permanently locked, mirroring the reported "emergency withdraw reverts because reserve already consumed" bug class.

### Finding Description
`ExchangeCreateActuator.execute()` lets any account deposit `firstTokenBalance`/`secondTokenBalance` and creates an `ExchangeCapsule` that tracks only aggregate reserves - not per-user shares: [1](#0-0) 

Any user can then trade against the pool via `ExchangeTransactionActuator`, which calls `ExchangeCapsule.transaction()` and mutates the aggregate reserves using a bonding-curve (Bancor-like) formula: [2](#0-1) 

`ExchangeProcessor.exchangeFromSupply()` computes the amount of the "buy" token to release using floating point math and truncates it to a `long`: [3](#0-2) 

Because `buyTokenQuant` is derived from a `double` calculation and then truncated, a large enough `sellTokenQuant` on the last remaining units of the opposite reserve can make `buyTokenQuant` equal exactly the remaining `secondTokenBalance` (or `firstTokenBalance`), driving that reserve to precisely `0` through purely permissionless trading - no special privilege is required, this is the normal `ExchangeTransactionActuator` path reachable from any broadcast transaction: [4](#0-3) 

Once either side is zero, `ExchangeTransactionActuator.doValidate()` itself starts rejecting all further trades against the pool: [5](#0-4) 

The only remaining way for the creator to recover value is `ExchangeWithdrawActuator`, restricted to the creator address: [6](#0-5) 

But `doValidate()` unconditionally rejects withdrawal whenever either reserve is zero, treating the pool as permanently "closed": [7](#0-6) 

This exact revert condition is also confirmed by the existing unit test that manually sets balances to `(0,0)` and expects the "the exchange has been closed" validate exception: [8](#0-7) 

There is no other actuator (`ExchangeInjectActuator` only adds liquidity) or governance/admin recovery mechanism that can move the residual, non-zero reserve back to the creator. The remaining balance of whichever token did not hit zero is therefore permanently stranded inside the `Exchange`/`ExchangeV2` store entry, exactly analogous to the reported `LaunchEvent.emergencyWithdraw()` issue where a reserve variable being consumed by normal operation (`createPair`/legitimate trading) makes the only withdrawal path (`emergencyWithdraw`/`ExchangeWithdrawActuator`) revert and causes the depositor to lose their funds entirely.

### Impact Explanation
The exchange creator's initial capital contribution (and any residual, unimbalanced token value accumulated from trading) can become permanently unrecoverable. This is a concrete, on-chain, permanent loss-of-funds / accounting corruption bug reachable purely through normal broadcast transactions (`ExchangeCreateContract` + `ExchangeTransactionContract` + `ExchangeWithdrawContract`), with no privileged actor, admin key, or malicious peer required.

### Likelihood Explanation
Reaching this state requires driving one reserve to exactly zero through repeated/careful trades exploiting floating-point truncation in `ExchangeProcessor`, or via the extreme end of the bonding curve where the last remaining units of a reserve are bought out. This is plausible for exchanges with modest reserves or via deliberate manipulation by a trader who wants to grief a pool creator, and is entirely achievable without any special permission - it only requires ordinary `ExchangeTransactionContract` broadcasts.

### Recommendation
Add a recovery path in `ExchangeWithdrawActuator` (or a dedicated actuator) that allows the creator to withdraw the remaining non-zero-side balance proportionally or in full when the exchange is "closed" (one side is zero), instead of unconditionally reverting. Alternatively, prevent trades that would zero out a reserve entirely (e.g., enforce a minimum reserve floor in `ExchangeTransactionActuator.doValidate()`), avoiding creation of an unrecoverable state in the first place.

### Proof of Concept
1. Account A calls `ExchangeCreateContract` to create an exchange with `firstTokenBalance = X` and `secondTokenBalance = Y` (`ExchangeCreateActuator.execute`, lines 55-116).
2. Account B repeatedly calls `ExchangeTransactionContract` selling the first token, using `ExchangeCapsule.transaction()`'s bonding-curve math (`ExchangeProcessor.exchangeFromSupply`) until the computed `buyTokenQuant` in a final trade exactly equals the remaining `secondTokenBalance`, driving `secondTokenBalance` to `0`.
3. Any further `ExchangeTransactionContract` calls now revert with "Token balance in exchange is equal with 0, the exchange has been closed" (`ExchangeTransactionActuator.doValidate`, lines 194-197).
4. Account A (the creator) calls `ExchangeWithdrawContract` to recover the remaining `firstTokenBalance`; `ExchangeWithdrawActuator.doValidate()` reverts with the same "the exchange has been closed" message (lines 209-212), permanently blocking withdrawal.
5. No other actuator exists to move funds out of the `Exchange`/`ExchangeV2` store entry, so the remaining `firstTokenBalance` is permanently stuck.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L90-91)
```java
        exchangeCapsule.setBalance(firstTokenBalance, secondTokenBalance);
        exchangeStore.put(exchangeCapsule.createDbKey(), exchangeCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L31-39)
```java
  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeWithdrawActuatorTest.java (L1098-1111)
```java
    try {
      ExchangeCapsule exchangeCapsuleV2 = dbManager.getExchangeV2Store()
          .get(ByteArray.fromLong(exchangeId));
      exchangeCapsuleV2.setBalance(0, 0);
      dbManager.getExchangeV2Store().put(exchangeCapsuleV2.createDbKey(), exchangeCapsuleV2);

      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("Token balance in exchange is equal with 0,"
              + "the exchange has been closed",
          e.getMessage());
```
