This confirms the analog: `ExchangeCreateActuator` assigns a purely sequential, non-collision-resistant `exchangeId` (`getLatestExchangeNum() + 1`), reachable by any TRX/asset holder without any privileged role, and `ExchangeTransactionActuator`/`ExchangeInjectActuator` blindly trust `contract.getExchangeId()` to look up the pool and execute the trade against whatever token pair currently occupies that slot, with no binding (hash/commitment) between the ID and the original exchange's properties.

### Title
Sequentially-assigned, unbound exchangeId in ExchangeCreateActuator allows chain-reorg-induced trade misdirection - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java])

### Summary
`ExchangeCreateActuator` assigns each new TRX/TRC10 exchange pool an ID that is simply `getLatestExchangeNum() + 1` [1](#0-0) , then persists the counter with `dynamicStore.saveLatestExchangeNum(id)` [2](#0-1) . Any later transaction that references an exchange — `ExchangeTransactionContract`, `ExchangeInjectContract`, `ExchangeWithdrawContract` — only supplies this bare numeric `exchangeId` and the actuator fetches the pool purely by that ID via `Commons.getExchangeStoreFinal(...).get(ByteArray.fromLong(contract.getExchangeId()))` [3](#0-2) , with no binding to the token pair, creator, or creation time the sender intended when they signed and broadcast the transaction.

### Finding Description
This is the same bug class as the external report: an ID that is derived from a mutable, chain-state-dependent counter at execution time, rather than a value committed to (e.g., via a hash of the object's properties) at the moment the referencing transaction is signed. Because the ID assignment for exchange N happens only when the `ExchangeCreateContract` transaction is actually included and executed, its value is a function of blockchain ordering, not of anything the submitter or a downstream actor can pin down in advance.

If a chain re-organization occurs after a user broadcasts an `ExchangeCreateContract` (say, creating a TRX/USDT-like pool that would become exchange `#N`), and in the winning fork a different `ExchangeCreateContract` (a different token pair, submitted by anyone, including an attacker) is included first, that other transaction consumes exchange ID `#N` instead. Any transaction that was signed and broadcast referencing `exchangeId = N` — e.g., an `ExchangeTransactionContract` intended to trade against the original TRX/USDT pool — will, once included, blindly operate on whatever pool now occupies slot `#N`, per the actuator logic shown above. There is no re-validation that the traded token IDs match what the sender expected beyond checking that the token is one of the two tokens in the pool that currently exists at that ID (`!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)`) [4](#0-3) , which only weakly limits — but does not eliminate — the impact, since the counterpart ("another") token and its balance/price come entirely from the possibly-substituted pool.

`ExchangeInjectActuator` has a partial mitigation: it requires the sender to be the pool creator (`!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`) [5](#0-4) , which reduces (but does not fully eliminate, since the creator could be the same address across the reorg) exposure for that specific actuator. `ExchangeTransactionActuator`, however, has no such check — any account can trade against any `exchangeId`.

### Impact Explanation
An unprivileged, ordinary account (not a witness/SR, not requiring any special role — unlike the `ProposalCreateActuator`/`ProposalApproveActuator` governance path, which is restricted to witnesses and thus out of scope per the privileged-actor exclusion) can create or be the target of ID reuse in `ExchangeCreateActuator`. A trade transaction whose signer intended to swap into token X against the pool they expected can, after a reorg, be executed against an entirely different token pair, causing unintended asset transfers/losses and pool accounting corruption. This directly matches the "unauthorized account operation" / "asset or accounting corruption" impact categories.

### Likelihood Explanation
Exploitation requires a chain reorganization to occur at the exact moment an `ExchangeCreateContract` and a dependent `ExchangeTransactionContract`/`ExchangeInjectContract` are in flight — the same reorg-dependent precondition noted in the original report ("We've seen very large re-orgs in top blockchains such as Polygon"). TRON's DPoS consensus with witness-based block production generally has bounded/rare reorgs compared to some other chains, but the report explicitly acknowledges this remains a real threat class worth mitigating, and the code pattern (`getLatestExchangeNum() + 1` with no commitment hash) is structurally identical to the vulnerable Solidity pattern.

### Recommendation
Bind the `exchangeId` (and the analogous `proposalId` in `ProposalCreateActuator`, which has the identical pattern) to the properties of the object being created — e.g., derive from a hash of `(ownerAddress, firstTokenId, secondTokenId, createTime, latestExchangeNum)` — or otherwise require dependent transactions (`ExchangeTransactionContract`, `ExchangeInjectContract`) to additionally specify and validate the expected token pair/creator against the referenced exchange before executing, rejecting the transaction if they don't match, rather than trusting the bare numeric ID after a potential reorg.

### Proof of Concept
1. Account A broadcasts `ExchangeCreateContract` intending to create exchange `#N` for TRX/TokenX, expecting `getLatestExchangeNum()+1 == N` at broadcast time [6](#0-5) .
2. Account A (or another user) immediately broadcasts a dependent `ExchangeTransactionContract` with `exchangeId = N`, `tokenId = TRX`, intending to swap TRX for TokenX [7](#0-6) .
3. A chain reorg replaces A's `ExchangeCreateContract` with attacker's `ExchangeCreateContract` for TRX/TokenY, which is confirmed first and consumes ID `#N` via the same `getLatestExchangeNum()+1` logic.
4. The pending `ExchangeTransactionContract` (still valid/broadcastable) is included, executing against exchange `#N` — now TRX/TokenY — silently swapping the victim's TRX for TokenY instead of TokenX, at whatever price/liquidity the attacker's pool has, causing an unintended and potentially unfavorable trade/loss for the victim.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L78-91)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L118-119)
```java
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
      dynamicStore.saveLatestExchangeNum(id);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L52-59)
```java
      final ExchangeTransactionContract exchangeTransactionContract = this.any
          .unpack(ExchangeTransactionContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeTransactionContract.getOwnerAddress().toByteArray());

      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L182-184)
```java
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```
