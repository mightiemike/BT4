### Title
Honest trader's exchange swap can be misdirected to an attacker-controlled exchange after a chain reorg due to sequential, content-independent Exchange ID assignment - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
`ExchangeCreateActuator` assigns each newly created bancor-style exchange a purely sequential ID derived from a global counter (`dynamicStore.getLatestExchangeNum() + 1`), independent of the transaction's content (creator, token pair, timestamp, etc.) [1](#0-0) . A subsequent `ExchangeTransactionContract` only references the exchange by this numeric `exchangeId` and validates the trade solely against whatever exchange currently occupies that ID slot [2](#0-1) . This is structurally the same class of bug as the Optimism `DisputeGameFactory`/`FaultDisputeGame` finding: an identifier that is assigned by transaction *order* rather than by transaction *content* can end up bound to a different real-world object after a block re-org reorders the creating transactions, silently redirecting a party's later action (the "move", here the "trade") to the wrong target.

### Finding Description
When `ExchangeCreateActuator.execute()` runs, it computes the exchange ID as `id = dynamicStore.getLatestExchangeNum() + 1` and persists the new `ExchangeCapsule` under that ID [3](#0-2) . This ID has no cryptographic binding to the creator's address, the specific token pair, or the originating transaction hash — it is purely a position in a global sequence, exactly like the nonce-derived `CREATE` address in the Optimism report.

`ExchangeTransactionActuator.doValidate()` and `execute()` fetch the target exchange purely by this numeric ID:
```
exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
    .get(ByteArray.fromLong(contract.getExchangeId()));
``` [2](#0-1) 
It then checks only that the trader's `tokenID` is one of the two tokens currently registered on that exchange, and that the computed output meets the trader's `expected` minimum [4](#0-3) . Critically, the counter-asset (`anotherTokenID`) that the trader will actually receive is derived internally from whichever exchange now sits at that ID — the trader never specifies or the actuator never verifies which specific counter-asset was intended [5](#0-4) .

Analogous to the Optimism scenario: if two `ExchangeCreateContract` transactions (one legitimate, one attacker-crafted with the same first token, e.g. TRX, but a worthless/attacker-controlled second token) are broadcast, and a reorg changes their relative order, the ID that was going to be assigned to the legitimate exchange can instead be assigned to the attacker's exchange (or vice versa). A pending `ExchangeTransactionContract` from an honest trader — signed/broadcast before the reorg, referencing that ID and a `tokenID`/`expected` that are satisfiable on both exchanges — will pass `doValidate()` against the attacker's exchange instead of the intended one, because the actuator never checks the counter-asset identity, only the input token identity and minimum output quantity.

### Impact Explanation
An honest trader can have their swap silently executed against an attacker-substituted exchange after a chain reorg, receiving an attacker-chosen worthless asset instead of the intended one while still spending their real TRX/TRC10 tokens — a direct asset/accounting-corruption outcome triggered purely by transaction reordering, with no privileged access required by the attacker (any account can call `ExchangeCreateContract`/`ExchangeTransactionContract`).

### Likelihood Explanation
Requires a chain reorg to occur while an exchange-creation and exchange-trade pair are in flight, and requires an attacker to have pre-staged a colliding `ExchangeCreateContract` designed to occupy the same sequential ID slot with a matching first-token type. This is a lower-likelihood, timing-dependent scenario (similar to the original Optimism finding's assessed severity), but it is a concrete, reachable path through unprivileged, ordinary broadcast transactions (`ExchangeCreateContract`, `ExchangeTransactionContract`) with no reliance on leaked keys, malicious peers, or privileged roles.

### Recommendation
Bind the exchange (and any similarly sequentially-ID'd on-chain object, e.g. proposals, asset IDs) not only to a monotonically increasing counter but also to content that cannot be swapped by reordering — e.g., derive/verify the ID (or an additional binding field) from the creating transaction's hash, and have `ExchangeTransactionActuator` validate that the fetched `ExchangeCapsule`'s token pair and/or creator match values the trader explicitly supplied (not just that the input token is present), so a reorg cannot silently redirect a trade to an unintended exchange instance.

### Proof of Concept
1. Attacker observes an honest user's pending `ExchangeCreateContract` (creating TRX/TokenA exchange) and prepares a competing `ExchangeCreateContract` for TRX/TokenB (attacker-controlled worthless token), timed to be included right before the honest user's transaction if a reorg occurs.
2. Honest user separately broadcasts an `ExchangeTransactionContract` referencing the expected `exchangeId` (the next sequential ID) with `tokenId = TRX`, `quant`, and `expected` computed against TokenA's exchange curve.
3. A reorg reorders the two `ExchangeCreateContract` transactions such that the attacker's TRX/TokenB exchange now receives the ID the honest user's transaction expected.
4. `ExchangeTransactionActuator.doValidate()` at [6](#0-5)  succeeds because `tokenID` (TRX) is still present on the attacker's exchange and the attacker calibrates the TokenB curve to still satisfy `tokenExpected`.
5. `execute()` at [7](#0-6)  credits the honest user with attacker's worthless TokenB instead of the intended TokenA, while debiting their real TRX.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L78-119)
```java
      long id = addExact(dynamicStore.getLatestExchangeNum(), 1);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
      if (dynamicStore.getAllowSameTokenName() == 0) {
        //save to old asset store
        ExchangeCapsule exchangeCapsule =
            new ExchangeCapsule(
                exchangeCreateContract.getOwnerAddress(),
                id,
                now,
                firstTokenID,
                secondTokenID
            );
        exchangeCapsule.setBalance(firstTokenBalance, secondTokenBalance);
        exchangeStore.put(exchangeCapsule.createDbKey(), exchangeCapsule);

        //save to new asset store
        if (!Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
          String firstTokenRealID = assetIssueStore.get(firstTokenID).getId();
          firstTokenID = firstTokenRealID.getBytes();
        }
        if (!Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
          String secondTokenRealID = assetIssueStore.get(secondTokenID).getId();
          secondTokenID = secondTokenRealID.getBytes();
        }
      }

      {
        // only save to new asset store
        ExchangeCapsule exchangeCapsuleV2 =
            new ExchangeCapsule(
                exchangeCreateContract.getOwnerAddress(),
                id,
                now,
                firstTokenID,
                secondTokenID
            );
        exchangeCapsuleV2.setBalance(firstTokenBalance, secondTokenBalance);
        exchangeV2Store.put(exchangeCapsuleV2.createDbKey(), exchangeCapsuleV2);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
      dynamicStore.saveLatestExchangeNum(id);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-98)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L159-221)
```java
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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
