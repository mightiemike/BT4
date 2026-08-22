### Title
Missing vote-merging in `VoteWitnessActuator` allows duplicate witness `Vote` entries in an account's vote list - (File: actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java)

### Summary
The report describes the root cause pattern: an entry is added to an accounting list without first checking/normalizing whether that key already exists, allowing duplicate entries for the same identity to accumulate and be processed multiple times downstream. In java-tron, the analogous code path is the legacy (non-TVM) `VoteWitnessActuator.countVoteAccount`, which appends every submitted `Vote` directly to the account's votes list without deduplicating/merging votes for the same witness address, unlike its TVM sibling which explicitly merges duplicates.

### Finding Description
`VoteWitnessActuator.countVoteAccount` builds the new votes for an account directly from the transaction's vote list: [1](#0-0) 

For each `Vote` in `voteContract.getVotesList()`, it calls `accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount())` unconditionally, and `addVotes` simply appends a new `Vote` protobuf entry to the account's votes list with no check for an existing entry with the same `voteAddress`: [2](#0-1) 

`validate()` only checks that the *total* vote count across all `Vote` entries does not exceed the voter's `tronPower`; it does not require unique witness addresses: [3](#0-2) 

This is exactly the bug class in the report: a legitimate transaction (`VoteWitnessContract` broadcast by any account) can list the same witness address multiple times (up to `MAX_VOTE_NUMBER`), and the code has no "already present" check before appending, so the account's on-chain `votes` list ends up with multiple `Vote` entries referring to the same witness.

Contrast this with the newer TVM-triggered path (`VoteWitnessProcessor.execute`), which explicitly merges votes for the same witness into a `Map<ByteString, Long>` before writing them back — precisely the kind of "self/duplicate" check that is missing in the legacy actuator: [4](#0-3) 

### Impact Explanation
The duplicated `Vote` entries for the same witness are the values that (a) `MaintenanceManager.countVote` iterates when tallying total votes per witness during epoch maintenance, and (b) `MortgageService.computeReward`/`VoteRewardUtil.computeReward` iterate when computing a voter's proportional reward per cycle: [5](#0-4) [6](#0-5) 

Because both of these downstream consumers iterate the raw `Vote` list and accumulate `voteCount` per entry, mathematically the impact is limited: splitting a witness's total vote count across N duplicate entries with the same `voteAddress` sums to the same total (additive, no double-counting or underflow), unlike the report's `remainingAmount = amount - totalDelegatedAmount` scenario, which subtracts a duplicated value and can underflow/revert. I could not identify a concrete arithmetic path in java-tron where this duplication causes a subtraction-based underflow, balance-lock, or consensus-divergent double count comparable to the reported severity — the reachable effect here is limited to redundant/bloated `Vote` entries in on-chain storage (`Account.votes`, `VotesCapsule`), which increases storage bloat and iteration cost proportional to `MAX_VOTE_NUMBER`, and inconsistent internal representation between the legacy actuator and the TVM processor (which normalizes duplicates) for the same logical operation.

### Likelihood Explanation
This requires only an ordinary, anonymous `VoteWitnessContract` broadcast transaction listing the same witness address multiple times with the sum of vote counts within the voter's `tronPower` and `MAX_VOTE_NUMBER` — no privileged actor, leaked key, or malicious peer is needed. It is trivially reachable from any account with frozen/tron-power balance.

### Recommendation
In `VoteWitnessActuator.countVoteAccount`, merge duplicate witness votes into a map (as already done in `VoteWitnessProcessor.execute`) before calling `accountCapsule.addVotes`/`votesCapsule.addNewVotes`, ensuring at most one `Vote` entry per witness address is persisted, consistent with the TVM vote path.

### Proof of Concept
1. Freeze balance for account `A` to obtain `tronPower`.
2. Broadcast a `VoteWitnessContract` from `A` containing two `Vote` entries for the same witness `W`: `{voteAddress: W, voteCount: v1}` and `{voteAddress: W, voteCount: v2}`, with `(v1+v2)*TRX_PRECISION <= A.tronPower` and total vote entries `<= MAX_VOTE_NUMBER` (see the existing test `vote1WitnssOneMoreTiems` which already demonstrates repeated votes to the same witness being summed via `getRepeateContract`): [7](#0-6) 
3. Inspect `A`'s `AccountCapsule.getVotesList()` after execution — it will contain two separate `Vote` entries for `W` instead of one merged entry, confirming the missing de-duplication identified in `VoteWitnessActuator.countVoteAccount` versus `VoteWitnessProcessor.execute`.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L178-190)
```java
    accountCapsule.clearVotes();
    votesCapsule.clearNewVotes();

    voteContract.getVotesList().forEach(vote -> {
      logger.debug("countVoteAccount, address[{}]",
          ByteArray.toHexString(vote.getVoteAddress().toByteArray()));

      votesCapsule.addNewVotes(vote.getVoteAddress(), vote.getVoteCount());
      accountCapsule.addVotes(vote.getVoteAddress(), vote.getVoteCount());
    });

    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
    votesStore.put(ownerAddress, votesCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L574-585)
```java
  /**
   * set votes.
   */
  public void addVotes(ByteString voteAddress, long voteAdd) {
    this.account = this.account.toBuilder()
        .addVotes(Vote.newBuilder().setVoteAddress(voteAddress).setVoteCount(voteAdd).build())
        .build();
  }

  public void addAllVotes(List<Vote> votesToAdd) {
    this.account = this.account.toBuilder().addAllVotes(votesToAdd).build();
  }
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

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L181-189)
```java
      votes.getNewVotes().forEach(vote -> {
        ByteString voteAddress = vote.getVoteAddress();
        long voteCount = vote.getVoteCount();
        if (countWitness.containsKey(voteAddress)) {
          countWitness.put(voteAddress, countWitness.get(voteAddress) + voteCount);
        } else {
          countWitness.put(voteAddress, voteCount);
        }
      });
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L199-230)
```java
  private long computeReward(long beginCycle, long endCycle, AccountCapsule accountCapsule) {
    if (beginCycle >= endCycle) {
      return 0;
    }

    long reward = 0;
    long newAlgorithmCycle = dynamicPropertiesStore.getNewRewardAlgorithmEffectiveCycle();
    List<Pair<byte[], Long>> srAddresses = accountCapsule.getVotesList().stream()
        .map(vote -> new Pair<>(vote.getVoteAddress().toByteArray(), vote.getVoteCount()))
        .collect(Collectors.toList());
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
    }
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long>  vote : srAddresses) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = delegationStore.getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = delegationStore.getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
    }
    return reward;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/VoteWitnessActuatorTest.java (L399-428)
```java
  /**
   * Vote 1 witness one more times.
   */
  @Test
  public void vote1WitnssOneMoreTiems() {
    long frozenBalance = 1_000_000_000_000L;
    long duration = 3;
    FreezeBalanceActuator freezeBalanceActuator = new FreezeBalanceActuator();
    freezeBalanceActuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(OWNER_ADDRESS, frozenBalance, duration));
    VoteWitnessActuator actuator = new VoteWitnessActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getRepeateContract(OWNER_ADDRESS, WITNESS_ADDRESS, 1L, 30));
    TransactionResultCapsule ret = new TransactionResultCapsule();
    try {
      freezeBalanceActuator.validate();
      freezeBalanceActuator.execute(ret);
      actuator.validate();
      actuator.execute(ret);

      maintenanceManager.doMaintenance();
      WitnessCapsule witnessCapsule = dbManager.getWitnessStore()
          .get(StringUtil.hexString2ByteString(WITNESS_ADDRESS).toByteArray());
      Assert.assertEquals(10 + 30, witnessCapsule.getVoteCount());
    } catch (ContractValidateException e) {
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
