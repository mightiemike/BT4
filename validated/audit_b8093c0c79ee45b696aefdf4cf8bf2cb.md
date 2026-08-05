### Title
Zero-effective-slippage sandwich attack on `ExchangeTransactionActuator` due to weak `expected` (minimum-output) validation - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
java-tron's on-chain bancor-style AMM (`Exchange`/`ExchangeV2`) lets any unprivileged account swap TRX/TRC10 tokens via `ExchangeTransactionContract`, processed by `ExchangeTransactionActuator`. The caller supplies a minimum-output parameter (`expected`), analogous to `_amountVoltMin` in the reported `VoltBurn.buyNSendToVoltTreasury()` bug. The actuator only requires `expected > 0` [1](#0-0)  and that the computed output is `>= expected` [2](#0-1) . Setting `expected = 1` (the minimum legal value) is functionally equivalent to zero slippage protection, exposing users to the exact sandwich-attack pattern described in the report.

### Finding Description
`ExchangeTransactionContract` carries `quant` (amount to sell) and `expected` (minimum amount to receive) [3](#0-2) . `ExchangeTransactionActuator.doValidate()` retrieves the pool state via `ExchangeCapsule`, computes the counter-token amount with the bancor curve implemented in `ExchangeProcessor`/`SafeExchangeProcessor`, and only rejects the trade if `anotherTokenQuant < tokenExpected` [2](#0-1) . The only other constraint on `expected` is that it must be strictly greater than zero [1](#0-0) .

This is callable by any unprivileged EOA with sufficient balance/asset — there is no owner-only gate, and the only checks are account existence, fee balance, token membership in the pool, and the pool's balance limit [4](#0-3) . Because `expected` can be set as low as `1`, a user (or an attacker crafting a victim's minimal-slippage transaction, or the victim themselves using default/minimal tooling) effectively has no real slippage protection, exactly mirroring the underlying flaw in the reported Solidity bug: an unbounded, caller-controlled minimum-output value that provides no meaningful protection against price manipulation between the front-run and back-run legs of a sandwich.

The pricing itself is a bancor/CPMM-like curve (`exchangeToSupply`/`exchangeFromSupply` in `ExchangeProcessor.java`) whose output is a deterministic function of the current pool balances [5](#0-4) , so an attacker who can front-run/back-run the victim's transaction (e.g., via block-producer transaction ordering, or via two of their own transactions bracketing the victim's in the same block) can shift the pool balances before the victim's trade executes, forcing the victim to receive close to their `expected` floor and extracting the difference back on the reverse trade.

### Impact Explanation
An attacker can extract value from any TRX↔TRC10 exchange trade submitted with a low `expected` value by sandwiching it: front-run by trading in the same direction to move the price, letting the victim's trade execute at the manipulated price (still satisfying `anotherTokenQuant >= expected` since `expected` is only required to be `> 0`), then reversing the front-run trade to capture the extracted value from the pool/victim. This causes direct economic loss (underpriced settlement) to any exchange-transaction caller who does not manually and correctly compute a protective `expected` value — a purely accounting/settlement impact matching a valid finding class (underpriced-public-work / settlement manipulation).

### Likelihood Explanation
The `Exchange`/`ExchangeV2` mechanism is a long-standing, publicly reachable feature of java-tron (`wallet/exchangetransaction` HTTP API and the `ExchangeTransactionContract` gRPC/transaction type) [6](#0-5) , callable by any account without special permission. Exploitation only requires normal transaction submission/ordering capability (no privileged role, no internal-only path), and the vulnerable condition (`expected` set too low, including the boundary case `expected = 1`) is the norm for naive integrations that don't compute a proper slippage bound, making this readily reachable by an unprivileged attacker.

### Recommendation
Enforce a meaningful minimum slippage tolerance rather than merely `expected > 0`. Options:
- Require `expected` to be within a bounded percentage (e.g., at least some min-out-to-quant ratio derived from current pool reserves) so trivial values like `1` are rejected.
- Alternatively, provide a chain-level/owner-configurable default maximum-allowed price impact per exchange transaction, similar to the report's recommendation of enforcing an arbitrary floor (e.g., 90%) when the caller does not provide adequate protection.
- Document/encourage wallets and SDKs to compute `expected` from current on-chain reserves at submission time rather than allowing arbitrary low values.

### Proof of Concept
1. Pool state: `Exchange` with `firstTokenBalance` (TRX) and `secondTokenBalance` (TRC10) as tracked in `ExchangeCapsule` [7](#0-6) .
2. Attacker submits Tx A: sell a large amount of TRX into the pool (large `quant`, `expected=1`), shifting the price against the second token.
3. Victim's pending Tx B (e.g., `quant=100`, `expected=1`, as allowed by validation at lines 190-192) executes next in the same block, receiving a far worse rate than the pre-manipulation price — but still passes the `anotherTokenQuant >= tokenExpected` check since `expected=1` [2](#0-1) .
4. Attacker submits Tx C: sell the acquired TRC10 back into the pool, restoring the price and net-extracting the value taken from the victim's degraded execution, analogous to the report's numeric scenario (pool shift from 1000S/100V → attacker nets ~991 tokens after bracketing the victim's trade).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L142-216)
```java
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

    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** framework/src/test/java/org/tron/common/utils/client/utils/HttpMethed.java (L552-578)
```java
  public static HttpResponse exchangeTransaction(
      String httpNode,
      byte[] ownerAddress,
      Integer exchangeId,
      String tokenId,
      Long quant,
      Long expected,
      String fromKey) {
    try {
      final String requestUrl = "http://" + httpNode + "/wallet/exchangetransaction";
      JsonObject userBaseObj2 = new JsonObject();
      userBaseObj2.addProperty("owner_address", ByteArray.toHexString(ownerAddress));
      userBaseObj2.addProperty("exchange_id", exchangeId);
      userBaseObj2.addProperty("token_id", str2hex(tokenId));
      userBaseObj2.addProperty("quant", quant);
      userBaseObj2.addProperty("expected", expected);
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L106-112)
```java
  public long getFirstTokenBalance() {
    return this.exchange.getFirstTokenBalance();
  }

  public long getSecondTokenBalance() {
    return this.exchange.getSecondTokenBalance();
  }
```
