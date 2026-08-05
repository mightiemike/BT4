Based on my research, the `AccountPermissionUpdateActuator` (the most literal analog to `VestingSplitter.sol`'s "accounts array" pattern) is **not** vulnerable — it already explicitly rejects duplicate addresses via a `.distinct()` check [1](#0-0) , confirmed by the `addressNotDistinctInPermission` test [2](#0-1) .

However, I found a genuine analog in the same "unchecked duplicate address array" bug class in `VoteWitnessActuator`.

### Title
Missing deduplication of duplicate witness addresses in `VoteWitnessContract.votes` list allows inconsistent account vote state - (File: actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java)

### Summary
`VoteWitnessActuator` accepts a `votes` array (list of `Vote{address, count}`) from an unprivileged user and applies each entry to the account's persisted vote list without merging duplicate witness addresses, unlike the parallel TVM-triggered code path (`VoteWitnessProcessor`), which explicitly deduplicates/merges votes for the same witness address before applying them.

### Finding Description
In `VoteWitnessActuator.countVoteAccount()`, the incoming `voteContract.getVotesList()` is iterated with a plain `forEach`, calling `votesCapsule.addNewVotes(...)` and `accountCapsule.addVotes(...)` once per `Vote` entry, without checking for or merging duplicate `vote.getVoteAddress()` values: [3](#0-2) 

`validate()` only sums vote counts for the tronPower bound check and validates each `Vote` independently — it never rejects or normalizes duplicate witness addresses within the same `votes` array: [4](#0-3) 

By contrast, the newer TVM-invoked equivalent (`VoteWitnessProcessor`, used when a smart contract calls the native `voteWitness` precompile) explicitly builds a `Map<ByteString, Long>` to "merge vote for same witness" before applying votes, deliberately fixing this exact class of duplicate-entry issue: [5](#0-4) 

This shows the duplicate-address problem was recognized and patched in one execution path (VM/native contract) but left unaddressed in the original actuator path used for regular `VoteWitnessContract` transactions, exactly mirroring the `VestingSplitter.sol` pattern of an unvalidated `accounts` array permitting duplicates.

### Impact Explanation
Because duplicates are not merged, `AccountCapsule`'s persisted `votes` list and `VotesCapsule.newVotes` can end up containing multiple separate `Vote` entries for the same witness address instead of one consolidated entry. This breaks the implicit "one entry per witness per account" invariant that the TVM path enforces, creating a state-representation divergence between two implementations of the same on-chain operation (`VoteWitnessContract`). Since `MaintenanceManager.countVote()` and `VoteRewardUtil.computeReward()` sum by address using maps or perform per-entry linear multiplication, the aggregate vote count/reward arithmetic itself is not directly over/under-credited (sums are still bounded by the `LongMath.checkedAdd`/`checkedMultiply` tronPower check). The concrete, provable impact is therefore a **state/accounting invariant divergence**: an account's stored vote list can contain redundant duplicate entries that other code assumes are deduplicated, inconsistent with the TVM path's guarantee, and inconsistent with data displayed via APIs (e.g., `Wallet.getVotesList` in `countVote`, `getAccount` responses) that were designed under the one-entry-per-witness assumption.

I was not able to fully verify with certainty whether any other downstream consumer (e.g., JSON-RPC serialization limits, list-size assumptions bounded at `MAX_VOTE_NUMBER=30`) relies more strictly on this invariant in a way that produces a stronger exploit (such as bypassing the 30-witness vote limit meaningfully) — the tool budget was exhausted before I could inspect `AccountCapsule.addVotes` / `VotesCapsule.addNewVotes` source directly to confirm whether they perform any dedup themselves at a lower layer.

### Likelihood Explanation
Any unprivileged account holder can trigger this by submitting a normal `VoteWitnessContract` transaction whose `votes` array (up to `MAX_VOTE_NUMBER = 30` entries, per [6](#0-5) ) repeats the same witness address multiple times with the total still bounded by their tronPower — no special privileges, contract deployment, or unusual conditions are required.

### Recommendation
In `VoteWitnessActuator.validate()` and/or `countVoteAccount()`, deduplicate/merge `Vote` entries by `vote.getVoteAddress()` before applying them to `accountCapsule` and `votesCapsule`, mirroring the `voteMap` merging logic already implemented in `VoteWitnessProcessor` [7](#0-6) , so both execution paths produce consistent, deduplicated vote state.

### Proof of Concept
1. Freeze balance for `OWNER_ADDRESS` to obtain sufficient tronPower (as in existing test `voteWitness`, using `FreezeBalanceActuator`) [8](#0-7) .
2. Construct a `VoteWitnessContract` whose `votes` list repeats the same `WITNESS_ADDRESS` several times (e.g., 3 entries of 1 vote each), similar to the existing `getRepeateContract` helper used in `voteCountsTest`/`vote1WitnssOneMoreTiems` [9](#0-8) , keeping total votes within tronPower and count ≤ 30.
3. Call `actuator.validate()` then `actuator.execute(ret)` — both succeed with `code.SUCESS`.
4. Inspect `dbManager.getAccountStore().get(OWNER_ADDRESS).getVotesList()` — it will contain 3 separate `Vote` entries for the same witness address instead of 1 merged entry with the summed count, unlike the deduplicated result produced by `VoteWitnessProcessor` when the same scenario is triggered via a smart contract call.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/AccountPermissionUpdateActuator.java (L96-104)
```java
    List<ByteString> addressList = permission.getKeysList()
        .stream()
        .map(x -> x.getAddress())
        .distinct()
        .collect(toList());
    if (addressList.size() != permission.getKeysList().size()) {
      throw new ContractValidateException(
          "address should be distinct in permission " + permission.getType());
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/AccountPermissionUpdateActuatorTest.java (L704-725)
```java
  @Test
  public void addressNotDistinctInPermission() {
    ByteString address = ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS));

    Permission ownerPermission = AccountCapsule.createDefaultOwnerPermission(address);
    Permission activePermission = Permission.newBuilder().setType(PermissionType.Active)
        .setPermissionName("active")
        .setParentId(0).setThreshold(1)
        .addKeys(Key.newBuilder().setAddress(address).setWeight(1).build())
        .addKeys(Key.newBuilder().setAddress(address).setWeight(1).build()).build();

    List<Permission> activeList = new ArrayList<>();
    activeList.add(activePermission);

    AccountPermissionUpdateActuator actuator = new AccountPermissionUpdateActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(address, ownerPermission, null, activeList));
    TransactionResultCapsule ret = new TransactionResultCapsule();

    processAndCheckInvalid(actuator, ret, "address should be distinct in permission",
        "address should be distinct in permission Active");
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L93-97)
```java
    int maxVoteNumber = MAX_VOTE_NUMBER;
    if (contract.getVotesCount() > maxVoteNumber) {
      throw new ContractValidateException(
          "VoteNumber more than maxVoteNumber " + maxVoteNumber);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L98-121)
```java
    try {
      Iterator<Vote> iterator = contract.getVotesList().iterator();
      Long sum = 0L;
      while (iterator.hasNext()) {
        Vote vote = iterator.next();
        byte[] witnessCandidate = vote.getVoteAddress().toByteArray();
        if (!DecodeUtil.addressValid(witnessCandidate)) {
          throw new ContractValidateException("Invalid vote address!");
        }
        long voteCount = vote.getVoteCount();
        if (voteCount <= 0) {
          throw new ContractValidateException("vote count must be greater than 0");
        }
        String readableWitnessAddress = StringUtil.createReadableString(vote.getVoteAddress());
        if (!accountStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              ACCOUNT_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        if (!witnessStore.has(witnessCandidate)) {
          throw new ContractValidateException(
              WITNESS_EXCEPTION_STR + readableWitnessAddress + NOT_EXIST_STR);
        }
        sum = LongMath.checkedAdd(sum, vote.getVoteCount());
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L181-187)
```java
    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L54-108)
```java
    Map<ByteString, Long> voteMap = new HashMap<>();
    Iterator<Protocol.Vote> iterator = param.getVotes().iterator();
    try {
      long sum = 0;
      while (iterator.hasNext()) {
        Protocol.Vote vote = iterator.next();

        byte[] witnessAddress = vote.getVoteAddress().toByteArray();
        /*
          Already covered while doing maintenance in MaintenanceManager.java, for tvm performance,
          we remove the account check
         */
//        if (repo.getAccount(witnessAddress) == null) {
//          throw new ContractValidateException(
//              ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
//        }
        if (repo.getWitness(witnessAddress) == null) {
          throw new ContractExeException(
              WITNESS_EXCEPTION_STR + StringUtil.encode58Check(witnessAddress) + NOT_EXIST_STR);
        }

        long voteCount = vote.getVoteCount();
        if (voteCount < 0) {
          throw new ContractExeException("Vote count must not be less than 0");
        } else if (voteCount == 0) {
          iterator.remove();
        } else {
          sum = LongMath.checkedAdd(sum, voteCount);
          // merge vote for same witness
          voteMap.put(vote.getVoteAddress(),
              LongMath.checkedAdd(voteMap.getOrDefault(vote.getVoteAddress(), 0L), voteCount));
        }
      }

      long tronPower;
      if (repo.getDynamicPropertiesStore().supportUnfreezeDelay()
          && repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }
      sum =  LongMath.checkedMultiply(sum, TRX_PRECISION);
      if (sum > tronPower) {
        throw new ContractExeException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
    } catch (ArithmeticException e) {
      throw new ContractExeException(e.getMessage());
    }

    for (Map.Entry<ByteString, Long> entry : voteMap.entrySet()) {
      accountCapsule.addVotes(entry.getKey(), entry.getValue());
      votesCapsule.addNewVotes(entry.getKey(), entry.getValue());
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/VoteWitnessActuatorTest.java (L121-130)
```java
  private Any getRepeateContract(String address, String voteaddress, Long value, int times) {
    VoteWitnessContract.Builder builder = VoteWitnessContract.newBuilder();
    builder.setOwnerAddress(StringUtil.hexString2ByteString(address));
    for (int i = 0; i < times; i++) {
      builder.addVotes(Vote.newBuilder()
          .setVoteAddress(StringUtil.hexString2ByteString(voteaddress))
          .setVoteCount(value).build());
    }
    return Any.pack(builder.build());
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/VoteWitnessActuatorTest.java (L135-167)
```java
  @Test
  public void voteWitness() {
    long frozenBalance = 1_000_000_000_000L;
    long duration = 3;
    FreezeBalanceActuator freezeBalanceActuator = new FreezeBalanceActuator();
    freezeBalanceActuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, frozenBalance, duration));
    VoteWitnessActuator actuator = new VoteWitnessActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, WITNESS_ADDRESS, 1L));
    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      freezeBalanceActuator.validate();
      freezeBalanceActuator.execute(ret);
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(1,
          dbManager.getAccountStore().get(ByteArray.fromHexString(OWNER_ADDRESS)).getVotesList()
              .get(0).getVoteCount());
      Assert.assertArrayEquals(ByteArray.fromHexString(WITNESS_ADDRESS),
          dbManager.getAccountStore().get(ByteArray.fromHexString(OWNER_ADDRESS)).getVotesList()
              .get(0).getVoteAddress().toByteArray());
      Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
      maintenanceManager.applyBlock(new BlockCapsule(Block.newBuilder().build()));
      WitnessCapsule witnessCapsule = dbManager.getWitnessStore()
          .get(StringUtil.hexString2ByteString(WITNESS_ADDRESS).toByteArray());
      Assert.assertEquals(10 + 1, witnessCapsule.getVoteCount());
    } catch (ContractValidateException e) {
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
