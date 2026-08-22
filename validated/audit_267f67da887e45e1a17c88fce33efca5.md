### Title
Front-running the `ExchangeBalanceLimit` proposal can retroactively DoS `ExchangeInjectActuator`/`ExchangeTransactionActuator` for existing exchange pairs - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java)

### Summary
`EXCHANGE_BALANCE_LIMIT` is a global, committee-adjustable `DynamicPropertiesStore` parameter that caps each token side of a bancor-style exchange pair. `ExchangeCreateActuator`, `ExchangeInjectActuator` and `ExchangeTransactionActuator` all read this single global cap at validation time and reject the transaction if either token balance would exceed it. Because the parameter is mutable and applies uniformly to all existing exchange pairs, a user can race a witness-approved parameter reduction to inject/trade into a pool right up to the old limit, and once the lower limit takes effect, legitimate `inject`/`transaction` operations on that pool become permanently blocked until the limit is raised again or balances are reduced (which itself may be blocked by the same check).

### Finding Description
`ExchangeInjectActuator.doValidate()` and `ExchangeTransactionActuator.doValidate()` compute the new pool balances after the operation and compare them against the single global `dynamicStore.getExchangeBalanceLimit()`: [1](#0-0) 

This exact pattern is reused in `ExchangeCreateActuator`: [2](#0-1) 

and in `ExchangeTransactionActuator` (evidenced by the identical error message/limit checks exercised in `ExchangeTransactionActuatorTest`) [3](#0-2) .

`EXCHANGE_BALANCE_LIMIT` is stored in and read from `DynamicPropertiesStore` and is adjustable via committee proposal (`ProposalUtil` references the parameter as a settable proposal type), meaning any committee-approved value change is visible to all nodes as soon as the proposal-carrying block/maintenance cycle is processed. Unlike per-account operator-only settings, this check applies retroactively to every existing exchange pool: once the global limit is lowered below an existing pool's current token balance, the `if (newTokenBalance > balanceLimit || ...) revert` check in both `inject` and `transaction` paths becomes unsatisfiable in the "increase" direction for that pool, and the check also blocks `ExchangeTransactionActuator` trades that would push either side's post-trade balance above the (now lower) limit—this can occur even for trades that shrink one side but grow the other side of the pair, since the check applies to `newTokenBalance` and `newAnotherTokenBalance` symmetrically as seen in `ExchangeInjectActuator.doValidate()` at lines 233-236 above.

This is directly analogous to the reported Enigma Vault issue: a mutable global cap (`maxTotalSupply` / `EXCHANGE_BALANCE_LIMIT`) is checked against an accumulating balance (`totalSupply()` / pool token balance) inside a user-facing state-changing entrypoint (`deposit()` / `inject`/`transaction`), and the cap can be changed independently of, and asynchronously with, ongoing user operations that increase the tracked balance.

### Impact Explanation
Once a pool's token balance exceeds a newly-lowered `EXCHANGE_BALANCE_LIMIT`, further `ExchangeInjectActuator` and `ExchangeTransactionActuator` calls that would increase either side of that pair are permanently rejected with `ContractValidateException("token balance must less than " + balanceLimit)`, denying legitimate users the ability to inject liquidity or execute trades on that exchange pair. This is a protocol-level (not merely a single contract's) DoS, since `Exchange`/`ExchangeV2` pairs are core TRC10 AMM primitives directly reachable via broadcast transactions, unlike the third-party Enigma Vault contract. The severity is bounded because it does not corrupt state or funds — it only blocks new inject/trade operations in the affected direction until the limit is raised again or the fork/committee reverses the change.

### Likelihood Explanation
Exploitation requires only broadcasting an `ExchangeInjectContract`/`ExchangeTransactionContract`, no privileged role is needed by the attacker (only the committee needs to approve a limit-lowering proposal, which is a normal governance action, not an attacker action). However, likelihood is moderate-to-low in practice: proposal state changes on java-tron take effect only after being approved by >=80% super representatives and applied at a maintenance cycle boundary (not instantly upon broadcast like a single-transaction front-run), so the "front-running window" is much larger and more predictable than the Enigma Vault's per-transaction race, making it easier for an attacker to react but also easier for the committee/community to anticipate and mitigate before the change activates.

### Recommendation
- Do not apply a newly-lowered `EXCHANGE_BALANCE_LIMIT` retroactively to existing pools; either grandfather existing exchange pairs created before the change, or only enforce the cap on `ExchangeCreateActuator` (pool creation) rather than on every subsequent `inject`/`transaction` call.
- Alternatively, when checking the limit in `ExchangeInjectActuator`/`ExchangeTransactionActuator`, only reject operations that would increase a balance that is already under the limit, and always allow operations that keep the balance from increasing further (i.e., allow trades but block only injections that would exceed the cap), so users are never fully locked out of an existing pool.
- Ensure the proposal/maintenance-cycle documentation and monitoring make clear when `EXCHANGE_BALANCE_LIMIT` reductions are pending, so operators of pools near the new limit can react before the change activates.

### Proof of Concept
1. Committee currently has `EXCHANGE_BALANCE_LIMIT = 1_000_000_000_000_000` (default seen in tests) [4](#0-3) .
2. A committee member proposes lowering `EXCHANGE_BALANCE_LIMIT` to a much smaller value.
3. Before/while the proposal is pending activation, an attacker (or any user) submits `ExchangeInjectContract` transactions to push an existing pool's token balance close to the current (higher) limit.
4. After the proposal activates, `ExchangeInjectActuator.doValidate()`'s check `if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit)` now fails for essentially all further inject operations on that pool [1](#0-0) , and `ExchangeTransactionActuator` trades that would grow either side similarly fail, matching the assertions in `ExchangeInjectActuatorTest.SameTokenNameCloseTokenBalanceGreaterThanBalanceLimit` and `ExchangeTransactionActuatorTest.SameTokenNameOpenTokenBalanceGreaterThanBalanceLimit`, which show the exact `"token balance must less than " + balanceLimit` rejection path being triggered by a balance exceeding the configured limit [5](#0-4) [6](#0-5) .

### Citations

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

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1315-1330)
```java
    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, 1));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("token balance must less than 1000000000000000",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeInjectActuatorTest.java (L1265-1272)
```java
  public void SameTokenNameCloseTokenBalanceGreaterThanBalanceLimit() {
    dbManager.getDynamicPropertiesStore().saveAllowSameTokenName(0);
    InitExchangeBeforeSameTokenNameActive();
    long exchangeId = 2;
    String firstTokenId = "_";
    long firstTokenQuant = 1_000_000_000_000_001L;
    String secondTokenId = "def";
    long secondTokenQuant = 400000000L;
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeInjectActuatorTest.java (L1284-1296)
```java
    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail();
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("token balance must less than 1000000000000000",
          e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    } finally {
```
