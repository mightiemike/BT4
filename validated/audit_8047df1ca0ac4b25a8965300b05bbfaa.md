Confirmed — this validates the analog. Both `ExchangeWithdrawContract` and `ExchangeInjectContract` compute a counterpart-token amount from the *current* pool ratio at execution time with no user-supplied minimum/maximum bound, unlike `ExchangeTransactionContract` which explicitly carries an `expected` field for slippage protection.

### Title
Missing Slippage Protection in `ExchangeWithdrawContract`/`ExchangeInjectContract` Allows Pool-Ratio Front-Running Loss - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
The TRON on-chain AMM-style bancor exchange (`ExchangeWithdrawContract`/`ExchangeInjectContract`, executed by `ExchangeWithdrawActuator`/`ExchangeInjectActuator`) computes the counterpart token amount (`anotherTokenQuant`) using the exchange pool's `firstTokenBalance`/`secondTokenBalance` ratio *at the time the transaction is executed*, not at the time it was signed and broadcast. Unlike `ExchangeTransactionContract`, which carries an explicit `expected` field for minimum-received slippage protection, neither `ExchangeWithdrawContract` nor `ExchangeInjectContract` provide any user-supplied bound on the counterpart amount.

### Finding Description
In `ExchangeWithdrawActuator.doValidate()` and `.execute()`, the withdrawer specifies only `tokenID` and `tokenQuant` (the amount of one token to withdraw). The amount of the other pooled token returned, `anotherTokenQuant`, is derived purely from the pool's live balances: [1](#0-0) 

This ratio calculation is repeated, without any caller-supplied bound, in `doValidate()`: [2](#0-1) 

Compare this to the protocol's own `ExchangeTransactionContract`, which was explicitly designed with a minimum-output slippage guard (`expected`) and enforces it at validation time (`"token required must greater than expected"`): [3](#0-2) [4](#0-3) 

Because `ExchangeWithdrawContract`/`ExchangeInjectContract` have no equivalent field, if another `ExchangeTransactionContract` (a trade against the same pool) is included in an earlier position within the same block or a preceding block after the withdraw/inject transaction was signed and broadcast, the pool ratio (`firstTokenBalance`/`secondTokenBalance`) shifts before the withdraw/inject actually executes. The withdrawer/injector then receives (or must supply) a `anotherTokenQuant` computed off the new, moved ratio — with no on-chain recourse to abort or bound the loss. This is the exact bug class from the referenced PoolTogether report: a value computed from a mutable, front-runnable exchange rate at execution time, with no minimum-out/maximum-in parameter for the caller to protect against ratio drift between signing and inclusion.

### Impact Explanation
Any TRON account that creates a bancor-style exchange pair and later withdraws or injects liquidity can receive a materially different counterpart-token amount than intended if the pool ratio is moved by an intervening `ExchangeTransactionContract` (which any anonymous account can submit against the same `exchange_id`). This is a direct on-chain asset/accounting-correctness issue — the actuator computes and finalizes token transfers based on values that can be manipulated between transaction construction and execution, with no protection mechanism available to the caller, unlike the sibling `ExchangeTransactionContract` path.

### Likelihood Explanation
Exchange pools are actively tradable by any account via `ExchangeTransactionContract`, so ratio movement between a pending withdraw/inject transaction's broadcast and its inclusion is trivially achievable by observing the mempool and submitting a trade that shifts the ratio in the direction unfavorable to the pending withdraw/inject — a plain front-running/sandwich pattern requiring no special privilege, matching the "anonymous broadcast transaction" reachability bar.

### Recommendation
Add an `expected`/bound parameter to `ExchangeWithdrawContract` and `ExchangeInjectContract` (e.g., `expected_another_token_quant` minimum-out for withdraw and maximum-in for inject), and enforce it in `ExchangeWithdrawActuator.doValidate()`/`ExchangeInjectActuator.doValidate()` the same way `ExchangeTransactionActuator` enforces `expected`, so callers can bound their exposure to pool-ratio drift between transaction signing and execution.

### Proof of Concept
1. Alice creates an exchange pair with `firstTokenBalance = 100000000`, `secondTokenBalance = 200000000` and signs `ExchangeWithdrawContract{tokenID=first, quant=X}`, expecting `anotherTokenQuant ≈ 2X` per the current ratio, then broadcasts it.
2. While Alice's transaction is pending, Bob broadcasts an `ExchangeTransactionContract` trading against the same `exchange_id`, shifting the `firstTokenBalance`/`secondTokenBalance` ratio.
3. Bob's transaction is included first (e.g., by paying more or via network timing).
4. Alice's `ExchangeWithdrawContract` then executes against the *new* ratio in `ExchangeWithdrawActuator.execute()` [1](#0-0) , returning an `anotherTokenQuant` different from what Alice expected — with no field on `ExchangeWithdrawContract` for Alice to have specified a minimum acceptable amount, unlike the `expected` field available on `ExchangeTransactionContract`.

### Citations

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

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1544-1557)
```java
    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, expected + 1));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail("should not run here");
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("token required must greater than expected",
          e.getMessage());
```
