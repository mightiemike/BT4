## Analysis: "Unnecessary claim step" analog in java-tron

The Unikrn bug is about a two-step allocate-then-claim design where minted supply becomes permanently unspendable for certain addresses, with no recovery path. The closest analog in `Thankgoddavid56/java-tron--007` is the witness reward "allowance" mechanism combined with the guard-representative withdrawal ban.

### Title
Guard-representative block/vote rewards are minted into `allowance` but withdrawal is unconditionally and permanently blocked - ([File: actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java])

### Summary
Block rewards, standby-witness rewards, transaction-fee rewards, and vote rewards are unconditionally computed and credited to every witness's `allowance` field via `MortgageService.payReward`/`adjustAllowance`, regardless of whether that witness is one of the genesis "guard representative" (GR) witnesses. [1](#0-0)  However, both withdrawal paths that convert `allowance` into spendable `balance` — the `WithdrawBalanceActuator` (regular transaction) and the TVM native `WithdrawRewardProcessor` (contract-triggered) — explicitly reject any address matching a genesis-block witness ("guard representative") with no alternative mechanism to release the funds. [2](#0-1) [3](#0-2) 

### Finding Description
`MortgageService.payReward` credits brokerage/reward amounts into an account's `allowance` field unconditionally for any witness address, including genesis-block guard representatives, via `adjustAllowance`. [4](#0-3)  This value accumulates in the `AccountCapsule.allowance` protobuf field ("witness block producing allowance"). [5](#0-4) 

To move `allowance` into `balance` (making it usable/transferable), a witness must submit a `WithdrawBalanceContract`. `WithdrawBalanceActuator.validate()` explicitly checks whether `ownerAddress` matches any address in `CommonParameter.getInstance().getGenesisBlock().getWitnesses()` and throws a `ContractValidateException` if so, permanently rejecting the withdrawal. [6](#0-5)  The same check exists in the TVM-native `WithdrawRewardProcessor.validate()`, which is invoked by the `withdrawReward` TVM opcode via `Program.withdrawReward()`. [7](#0-6) [8](#0-7) 

There is no other actuator, native contract, or code path in the reachable production code that allows moving `allowance` accrued by a guard representative into spendable `balance`. `MortgageService.queryReward` and `VoteRewardUtil.queryReward` both continue to report and grow this value indefinitely, [9](#0-8)  so the value is computed, stored, and reported as if it were a real, spendable asset, but it is unconditionally unspendable for this class of addresses — mirroring the Unikrn defect where tokens are "created and allocated" but permanently "locked ... with no methods to take them out."

A regression test in the codebase (`isGR`) explicitly documents and asserts this exact behavior, confirming it is by design rather than a bug introduced accidentally, but it demonstrates the permanent-lock condition concretely. [10](#0-9) 

### Impact Explanation
Rewards accrued for guard-representative witnesses are real value (minted block rewards, standby rewards, transaction-fee-pool rewards, vote rewards) that is computed and tracked per-account, contributing to what wallets/explorers may report via `allowance`/`queryReward` as an account's holdings, yet it can never be converted into transferable `balance` through any exposed RPC/contract path. This is an accounting inconsistency: the value exists in state (and is included in reward computations distributed to voters through the VI-based delta mechanism) but is functionally dead weight for the GR account itself, permanently unusable — the same “tokens minted but unusable” pattern flagged in the source report, just triggered by a hardcoded guard-representative exclusion rather than a lost key.

### Likelihood Explanation
This triggers deterministically and automatically for any of the genesis-block witnesses configured in `genesis.block.witnesses` every time they produce a block or receive standby/vote rewards — no attacker action is required, and the condition is permanent by design of the `isGP` check present in both the classic actuator and the TVM native processor.

### Recommendation
If guard representatives are intentionally meant to never receive spendable rewards, the reward-accrual path (`MortgageService.payReward`, `payStandbyWitness`, `VoteRewardUtil.withdrawReward`) should skip crediting `allowance` for guard-representative addresses in the first place, rather than minting/accruing the value and then blocking its withdrawal at the very last step. This avoids creating state that is reported as an asset but is permanently unspendable, removing the ambiguity and accounting mismatch, analogous to the report's recommendation to eliminate the unnecessary claim step and directly reflect the true final state.

### Proof of Concept
1. Configure a node with a genesis-block witness address `GR1`.
2. Let the node produce blocks as `GR1` (or route votes to it) so `MortgageService.payBlockReward`/`payStandbyWitness`/`VoteRewardUtil.withdrawReward` credit `allowance` on `GR1`'s `AccountCapsule`, exactly as exercised in `WithdrawBalanceActuatorTest.isGR`. [11](#0-10) 
3. Broadcast a `WithdrawBalanceContract` (or trigger a TVM contract calling `withdrawReward`) from `GR1`.
4. Observe `ContractValidateException`: "... is a guard representative and is not allowed to withdraw Balance", confirming `allowance` is permanently unreachable while `queryReward`/`allowance` continues to report it as if it were spendable value. [12](#0-11)

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L69-87)
```java
  public void payBlockReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} block reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  public void payTransactionFeeReward(byte[] witnessAddress, long value) {
    logger.debug("Pay {} transaction fee reward {}.", Hex.toHexString(witnessAddress), value);
    payReward(witnessAddress, value);
  }

  private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    double brokerageRate = (double) brokerage / 100;
    long brokerageAmount = (long) (brokerageRate * value);
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L136-169)
```java
  public long queryReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return 0;
    }

    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (accountCapsule == null) {
      return 0;
    }
    if (beginCycle > currentCycle) {
      return accountCapsule.getAllowance();
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      return reward + accountCapsule.getAllowance();
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
    }
    return reward + accountCapsule.getAllowance();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L110-119)
```java
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    boolean isGP = CommonParameter.getInstance()
        .getGenesisBlock().getWitnesses().stream().anyMatch(witness ->
            Arrays.equals(ownerAddress, witness.getAddress()));
    if (isGP) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress
              + "] is a guard representative and is not allowed to withdraw Balance");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawRewardProcessor.java (L21-36)
```java
  public void validate(WithdrawRewardParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();

    boolean isGP = CommonParameter.getInstance()
        .getGenesisBlock().getWitnesses().stream().anyMatch(witness ->
            Arrays.equals(ownerAddress, witness.getAddress()));
    if (isGP) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + StringUtil.encode58Check(ownerAddress)
              + "] is a guard representative and is not allowed to withdraw Balance");
    }
  }
```

**File:** protocol/src/main/protos/core/Tron.proto (L170-171)
```text
  // witness block producing allowance
  int64 allowance = 0x0B;
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2329-2358)
```java
  public long withdrawReward() {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner, 0, null,
        "withdrawReward", nonce, null);

    WithdrawRewardParam param = new WithdrawRewardParam();
    param.setOwnerAddress(owner);
    param.setNowInMs(getTimestamp().longValue() * 1000);
    try {
      WithdrawRewardProcessor processor = new WithdrawRewardProcessor();
      processor.validate(param, repository);
      long allowance = processor.execute(param, repository);
      repository.commit();
      if (internalTx != null) {
        internalTx.setValue(allowance);
      }
      return allowance;
    } catch (ContractValidateException e) {
      logger.warn("TVM WithdrawReward: validate failure. Reason: {}", e.getMessage());
    } catch (ContractExeException e) {
      logger.warn("TVM WithdrawReward: execute failure. Reason: {}", e.getMessage());
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return 0;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/WithdrawBalanceActuatorTest.java (L203-248)
```java
  @Test
  public void isGR() {
    Witness w = Args.getInstance().getGenesisBlock().getWitnesses().get(0);
    byte[] address = w.getAddress();
    AccountCapsule grCapsule = new AccountCapsule(ByteString.copyFromUtf8("gr"),
        ByteString.copyFrom(address), AccountType.Normal, initBalance);
    dbManager.getAccountStore().put(grCapsule.createDbKey(), grCapsule);
    long now = System.currentTimeMillis();
    dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(now);

    try {
      dbManager.getMortgageService()
          .adjustAllowance(dbManager.getAccountStore(), address, allowance);
    } catch (BalanceInsufficientException e) {
      fail("BalanceInsufficientException");
    }
    AccountCapsule accountCapsule = dbManager.getAccountStore().get(address);
    Assert.assertEquals(accountCapsule.getAllowance(), allowance);

    WitnessCapsule witnessCapsule = new WitnessCapsule(ByteString.copyFrom(address), 100,
        "http://google.com");

    dbManager.getAccountStore().put(address, accountCapsule);
    dbManager.getWitnessStore().put(address, witnessCapsule);

    WithdrawBalanceActuator actuator = new WithdrawBalanceActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ByteArray.toHexString(address)));

    TransactionResultCapsule ret = new TransactionResultCapsule();
    Assert.assertTrue(dbManager.getWitnessStore().has(address));

    try {
      actuator.validate();
      actuator.execute(ret);
      fail("cannot run here.");

    } catch (ContractValidateException e) {
      String readableOwnerAddress = StringUtil.createReadableString(address);
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("Account[" + readableOwnerAddress
          + "] is a guard representative and is not allowed to withdraw Balance", e.getMessage());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
