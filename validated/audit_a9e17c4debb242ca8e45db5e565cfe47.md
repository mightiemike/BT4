## Title
Guard Representative Accounts Permanently Accrue Unwithdrawable Rewards With No Sweep Mechanism - (File: `actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java`)

## Summary
Genesis Guard Representative (GR) accounts continue to accumulate block rewards / vote rewards into their `allowance` field through normal reward-accounting paths, but every code path that converts `allowance` into spendable `balance` explicitly rejects GR addresses, with no alternative sweep mechanism to recover the value. This mirrors the "shelter mechanism" bug class: an accounting subsystem keeps crediting an entity that has been intentionally excluded from the withdrawal path, permanently stranding value with no owner-controlled recovery function.

## Finding Description
`WithdrawBalanceActuator.validate()` unconditionally blocks any address matching a genesis witness (`isGP`) from calling `WithdrawBalanceContract`: [1](#0-0) 

The TVM native equivalent, `WithdrawRewardProcessor.validate()`, applies the identical GR check for the `withdrawReward()` opcode: [2](#0-1) 

These are the only two entry points that move accumulated reward out of `AccountCapsule.allowance` into `AccountCapsule.balance` (the field the rest of the protocol treats as spendable/transferable): [3](#0-2) 

Meanwhile, the reward-accrual paths (`payReward`/`payBlockReward`/`payStandbyWitness` and `MortgageService.adjustAllowance`) place no restriction on GR addresses — they keep crediting `allowance` for any witness address, GR or not: [4](#0-3) [5](#0-4) 

Block-level reward crediting in `Manager.payReward` also unconditionally adds to `account.getAllowance()` for the block-producing witness, again without any GR exclusion: [6](#0-5) 

The existing regression test confirms the resulting state: a GR account is credited allowance via `adjustAllowance`, but `WithdrawBalanceActuator.execute()` throws before any transfer, leaving the allowance stuck in place with no other actuator, opcode, or admin sweep function capable of moving it into `balance`. [7](#0-6) 

This is structurally identical to the reported bug class: a subsystem (`MortgageService`/`Manager.payReward`, analogous to `MasterChef.sol`) keeps allocating rewards to an entity (GR account, analogous to the sheltered pool) that has been carved out of the withdrawal flow (`WithdrawBalanceActuator`/`WithdrawRewardProcessor`, analogous to `Shelter.sol` blocking normal `ConvexStakingWrapper` claims), and there is no sweep/removal mechanism to recover the value once it becomes unreachable.

## Impact Explanation
Any block reward, standby-witness reward, transaction-fee reward, or TVM vote reward that ends up credited to a genesis GR address's `allowance` becomes permanently unreachable — it can never be converted to spendable `balance` through any protocol-exposed path, since both actuator- and TVM-level withdrawal routes explicitly reject GR addresses. This is a protocol-level, non-privileged loss of accounted value with no recovery mechanism, matching the Medium-severity "loss of yield with no sweep" classification of the original finding.

## Likelihood Explanation
This triggers automatically and deterministically whenever a genesis GR account is (or remains) an active witness receiving block rewards, or receives TVM vote rewards while `allowTvmVote` is enabled — no attacker action or privileged intervention is required beyond the GR configuration that already exists in the genesis block. The condition is exercised directly by the existing `isGR` unit test.

## Recommendation
Either exclude GR addresses from reward accrual (`payReward`/`payBlockReward`/`adjustAllowance`) to match the withdrawal restriction, or provide an alternate, safe mechanism (e.g., redirect GR rewards to a burn/treasury address at credit time, or allow a one-time reward sweep) so that accrued `allowance` for GR accounts does not become permanently stranded.

## Proof of Concept
1. Configure a genesis witness address as both a GR and an active `WitnessCapsule`.
2. Let block production proceed so `Manager.payReward` / `MortgageService.payBlockReward` repeatedly increments `account.allowance` for that address.
3. Attempt to reclaim the value via `WithdrawBalanceContract` (`WithdrawBalanceActuator`) or the TVM `withdrawReward()` opcode (`WithdrawRewardProcessor`) — both throw `ContractValidateException` ("is a guard representative and is not allowed to withdraw Balance") as shown in `WithdrawBalanceActuatorTest.isGR()`.
4. Observe that `allowance` keeps growing indefinitely with no code path able to move it into spendable `balance`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L54-68)
```java
    mortgageService.withdrawReward(withdrawBalanceContract.getOwnerAddress()
        .toByteArray());

    AccountCapsule accountCapsule = accountStore.
        get(withdrawBalanceContract.getOwnerAddress().toByteArray());
    long oldBalance = accountCapsule.getBalance();
    long allowance = accountCapsule.getAllowance();

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
        .setBalance(oldBalance + allowance)
        .setAllowance(0L)
        .setLatestWithdrawTime(now)
        .build());
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawBalanceActuator.java (L112-119)
```java
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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L232-258)
```java
  public void adjustAllowance(byte[] address, long amount) {
    try {
      if (amount <= 0) {
        return;
      }
      adjustAllowance(accountStore, address, amount);
    } catch (BalanceInsufficientException e) {
      logger.error("WithdrawReward error: {}.", e.getMessage());
    }
  }

  public void adjustAllowance(AccountStore accountStore, byte[] accountAddress, long amount)
      throws BalanceInsufficientException {
    AccountCapsule account = accountStore.getUnchecked(accountAddress);
    long allowance = account.getAllowance();
    if (amount == 0) {
      return;
    }

    if (amount < 0 && allowance < -amount) {
      throw new BalanceInsufficientException(
          String.format("%s insufficient balance, amount: %d, allowance: %d",
              StringUtil.createReadableString(accountAddress), amount, allowance));
    }
    account.setAllowance(allowance + amount);
    accountStore.put(account.createDbKey(), account);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1946-1985)
```java
  private void payReward(BlockCapsule block) {
    WitnessCapsule witnessCapsule =
        chainBaseManager.getWitnessStore().getUnchecked(block.getInstance().getBlockHeader()
            .getRawData().getWitnessAddress().toByteArray());
    if (getDynamicPropertiesStore().allowChangeDelegation()) {
      mortgageService.payBlockReward(witnessCapsule.getAddress().toByteArray(),
          getDynamicPropertiesStore().getWitnessPayPerBlock());
      mortgageService.payStandbyWitness();

      if (chainBaseManager.getDynamicPropertiesStore().supportTransactionFeePool()) {
        long transactionFeeReward = floorDiv(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool(),
                Constant.TRANSACTION_FEE_POOL_PERIOD,
            chainBaseManager.getDynamicPropertiesStore().disableJavaLangMath());
        mortgageService.payTransactionFeeReward(witnessCapsule.getAddress().toByteArray(),
            transactionFeeReward);
        chainBaseManager.getDynamicPropertiesStore().saveTransactionFeePool(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool()
                - transactionFeeReward);
      }
    } else {
      byte[] witness = block.getWitnessAddress().toByteArray();
      AccountCapsule account = getAccountStore().get(witness);
      account.setAllowance(account.getAllowance()
          + chainBaseManager.getDynamicPropertiesStore().getWitnessPayPerBlock());

      if (chainBaseManager.getDynamicPropertiesStore().supportTransactionFeePool()) {
        long transactionFeeReward = floorDiv(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool(),
                Constant.TRANSACTION_FEE_POOL_PERIOD,
            chainBaseManager.getDynamicPropertiesStore().disableJavaLangMath());
        account.setAllowance(account.getAllowance() + transactionFeeReward);
        chainBaseManager.getDynamicPropertiesStore().saveTransactionFeePool(
            chainBaseManager.getDynamicPropertiesStore().getTransactionFeePool()
                - transactionFeeReward);
      }

      getAccountStore().put(account.createDbKey(), account);
    }
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
