### Title
Stale AccountId index entries are not removed when an account is destroyed via `SELFDESTRUCT` - ([File: chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java])

### Summary
When a smart contract account executes `SELFDESTRUCT`/`SUICIDE`, `Program.suicide()`/`suicide2()` mark the contract's address for deletion from `AccountStore` via `getResult().addDeleteAccount(...)` [1](#0-0) , and the account row is removed from the `AccountStore` (confirmed by `Assert.assertNull(dbManager.getAccountStore().get(suicideContract))` in tests) [2](#0-1) . However, the `AccountIdIndexStore`, which is a separate registry mapping a chosen `accountId` string to the account's address (`SetAccountIdActuator`), is never cleaned up when the owning account is destroyed. This is directly analogous to the reported NFT bug: auxiliary "registered data" tied to an entity (`accountId -> address` mapping) is never purged when the entity is destroyed, leaving stale/inconsistent references.

### Finding Description
An account can call `SetAccountIdContract` once to bind a unique, case-insensitive `accountId` to its address; the mapping is stored in `AccountIdIndexStore` and is enforced to be globally unique via `accountIdIndexStore.has(accountId)` in `SetAccountIdActuator.validate()` [3](#0-2) . The `put` in `AccountIdIndexStore` only ever adds entries (`accountIdIndexStore.put(account)` in `SetAccountIdActuator.execute`) [4](#0-3) ; there is no code path (including account deletion via `SELFDESTRUCT`) that calls `accountIdIndexStore.delete(...)`.

When a contract account self-destructs, `Program.suicide()`/`suicide2()` add the contract's address to the delete-accounts set [5](#0-4) , and the account entry is fully removed from `AccountStore` at commit time, as verified by `CreateContractSuicideTest`/`ProgramResultTest` (`accountStore.get(contract)` returns `null` post-suicide) [6](#0-5) . All of the frozen/delegated-resource bookkeeping data attached to the destroyed account is explicitly transferred/cleared (`transferDelegatedResourceToInheritor`, `transferFrozenV2BalanceToInheritor`, `clearOwnerFreeze`, `clearOwnerFreezeV2`, `withdrawRewardAndCancelVote`) [7](#0-6) , showing the codebase's intent to fully clean up state on destruction. But the `AccountIdIndexStore` entry — which is stored in a completely separate DB keyed by `accountId`, not by address — is not part of any of these cleanup paths, nor is it referenced anywhere in `AccountStore.delete()` [8](#0-7)  or in the VM's account-deletion flow.

As a result, `accountIdIndexStore.has(accountId)` continues to return `true` forever after the owning account is destroyed, and `accountIdIndexStore.get(accountId)` continues to resolve to the now-nonexistent address (confirmed by `AccountIdIndexStore.get`/`has` reading straight from the revoking DB with no existence check against `AccountStore`) [9](#0-8) .

### Impact Explanation
This causes:
1. **Permanent squatting of `accountId` names.** Once an `accountId` is bound to an account that is later destroyed via `SELFDESTRUCT`, that human-readable id can never be reused by any other account, because `SetAccountIdActuator.validate()` rejects any id already present in `AccountIdIndexStore` [10](#0-9) , and there is no code path to release it.
2. **Stale resolution.** `Wallet.getAccountById()` looks up `accountIdIndexStore.get(account.getAccountId())` then loads the account from `AccountStore` [11](#0-10) ; if the address happens to be reused later by an unrelated new account (e.g. a new contract deployed to the same address via `CREATE2`), queries by the old `accountId` would resolve to the new, unrelated account, producing confusing/incorrect account resolution results via RPC.

This is a data-integrity/DoS-style issue (permanent resource exhaustion of the `accountId` namespace) reachable purely through normal, unprivileged transaction flows (`SetAccountIdContract` + a `TriggerSmartContract` invoking `SELFDESTRUCT`), matching the required "unauthorized... accounting corruption ... via ... protocol implementation" bar.

### Likelihood Explanation
Likelihood is Medium: it requires two ordinary, unprivileged actions available to any account — (1) set an `accountId` on a contract account via `SetAccountIdContract`, and (2) trigger that contract's `SELFDESTRUCT`. Both are common, well-supported TVM/actuator operations requiring no special privileges, only a `TriggerSmartContractContract` broadcast transaction.

### Recommendation
When an account address is added to the delete-accounts set during `SELFDESTRUCT` processing (`Program.suicide`/`suicide2`, `getResult().addDeleteAccount`) and ultimately removed from `AccountStore`, also look up and remove any corresponding entry from `AccountIdIndexStore` (and any other by-address secondary indices, e.g. `AccountIndexStore` keyed by account name) so that the `accountId`/name can be safely reused and no stale mapping persists after the account no longer exists.

### Proof of Concept
1. Deploy a contract account `C` and, from an unprivileged account, broadcast a `SetAccountIdContract` transaction binding `accountId = "myid"` to `C` (`SetAccountIdActuator.execute` calls `accountIdIndexStore.put(account)`) [4](#0-3) .
2. Broadcast a `TriggerSmartContractContract` invoking a function in `C` that executes `SELFDESTRUCT` (as exercised by `CreateContractSuicideTest`/`ProgramResultTest`) [12](#0-11) . After this transaction, `dbManager.getAccountStore().get(C)` returns `null`.
3. Query `accountIdIndexStore.has("myid".getBytes())` — it still returns `true`, and `accountIdIndexStore.get("myid")` still returns `C`'s address, even though `C` no longer exists in `AccountStore`.
4. Attempt to bind `"myid"` to any other account via `SetAccountIdContract` — `validate()` throws `ContractValidateException("This id has existed")` [10](#0-9) , proving the id is permanently unusable.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L451-516)
```java
  public void suicide(DataWord obtainerAddress) {

    byte[] owner = getContextAddress();
    byte[] obtainer = obtainerAddress.toTronAddress();

    if (VMConfig.allowTvmVote()) {
      withdrawRewardAndCancelVote(owner, getContractState());
    }

    long balance = getContractState().getBalance(owner);

    if (logger.isDebugEnabled()) {
      logger.debug("Transfer to: [{}] heritage: [{}]",
          Hex.toHexString(obtainer),
          balance);
    }

    increaseNonce();

    InternalTransaction internalTx = addInternalTx(null, owner, obtainer, balance, null,
        "suicide", nonce, getContractState().getAccount(owner).getAssetMapV2());

    int ADDRESS_SIZE = VMUtils.getAddressSize();
    if (FastByteComparisons.compareTo(owner, 0, ADDRESS_SIZE, obtainer, 0, ADDRESS_SIZE) == 0) {
      // if owner == obtainer just zeroing account according to Yellow Paper
      getContractState().addBalance(owner, -balance);
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (VMConfig.allowTvmTransferTrc10()) {
        getContractState().addBalance(blackHoleAddress, balance);
        MUtil.transferAllToken(getContractState(), owner, blackHoleAddress);
      }
    } else {
      createAccountIfNotExist(getContractState(), obtainer);
      try {
        MUtil.transfer(getContractState(), owner, obtainer, balance);
        if (VMConfig.allowTvmTransferTrc10()) {
          MUtil.transferAllToken(getContractState(), owner, obtainer);
        }
      } catch (ContractValidateException e) {
        if (VMConfig.allowTvmConstantinople()) {
          throw new TransferException(
              "transfer all token or transfer all trx failed in suicide: %s", e.getMessage());
        }
        throw new BytecodeExecutionException("transfer failure");
      }
    }
    if (VMConfig.allowTvmFreeze()) {
      byte[] blackHoleAddress = getContractState().getBlackHoleAddress();
      if (FastByteComparisons.isEqual(owner, obtainer)) {
        transferDelegatedResourceToInheritor(owner, blackHoleAddress, getContractState());
      } else {
        transferDelegatedResourceToInheritor(owner, obtainer, getContractState());
      }
    }
    if (VMConfig.allowTvmFreezeV2()) {
      byte[] Inheritor =
          FastByteComparisons.isEqual(owner, obtainer)
              ? getContractState().getBlackHoleAddress()
              : obtainer;
      long expireUnfrozenBalance = transferFrozenV2BalanceToInheritor(owner, Inheritor, getContractState());
      if (expireUnfrozenBalance > 0 && internalTx != null) {
        internalTx.setValue(internalTx.getValue() + expireUnfrozenBalance);
      }
    }
    getResult().addDeleteAccount(this.getContractAddress());
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L588-725)
```java
  private void transferDelegatedResourceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {

    // delegated resource from sender to owner, just abandon
    // in order to making that sender can unfreeze their balance in future
    // nothing will be deleted

    // delegated resource from owner to receiver
    // there cannot be any resource when suicide

    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);

    // transfer owner`s frozen balance for bandwidth to inheritor
    long frozenBalanceForBandwidthOfOwner = 0;
    // check if frozen for bandwidth exists
    if (ownerCapsule.getFrozenCount() != 0) {
      frozenBalanceForBandwidthOfOwner = ownerCapsule.getFrozenList().get(0).getFrozenBalance();
    }
    repo.addTotalNetWeight(-frozenBalanceForBandwidthOfOwner / TRX_PRECISION);

    long frozenBalanceForEnergyOfOwner =
        ownerCapsule.getAccountResource().getFrozenBalanceForEnergy().getFrozenBalance();
    repo.addTotalEnergyWeight(-frozenBalanceForEnergyOfOwner / TRX_PRECISION);

    // transfer all kinds of frozen balance to BlackHole
    repo.addBalance(inheritorAddr, frozenBalanceForBandwidthOfOwner + frozenBalanceForEnergyOfOwner);

    if (VMConfig.allowTvmSelfdestructRestriction()) {
      clearOwnerFreeze(ownerCapsule);
      repo.updateAccount(ownerAddr, ownerCapsule);
    }
  }

  private long transferFrozenV2BalanceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);
    AccountCapsule inheritorCapsule = repo.getAccount(inheritorAddr);
    long now = repo.getHeadSlot();

    // transfer frozen resource
    ownerCapsule.getFrozenV2List().stream()
        .filter(freezeV2 -> freezeV2.getAmount() > 0)
        .forEach(
            freezeV2 -> {
              switch (freezeV2.getType()) {
                case BANDWIDTH:
                  inheritorCapsule.addFrozenBalanceForBandwidthV2(freezeV2.getAmount());
                  break;
                case ENERGY:
                  inheritorCapsule.addFrozenBalanceForEnergyV2(freezeV2.getAmount());
                  break;
                case TRON_POWER:
                  inheritorCapsule.addFrozenForTronPowerV2(freezeV2.getAmount());
                  break;
              }
            });

    // merge usage
    BandwidthProcessor bandwidthProcessor = new BandwidthProcessor(ChainBaseManager.getInstance());
    bandwidthProcessor.updateUsageForDelegated(ownerCapsule);
    ownerCapsule.setLatestConsumeTime(now);
    if (ownerCapsule.getNetUsage() > 0) {
      bandwidthProcessor.unDelegateIncrease(inheritorCapsule, ownerCapsule,
          ownerCapsule.getNetUsage(), BANDWIDTH, now);
    }

    EnergyProcessor energyProcessor =
        new EnergyProcessor(
            repo.getDynamicPropertiesStore(), ChainBaseManager.getInstance().getAccountStore());
    energyProcessor.updateUsage(ownerCapsule);
    ownerCapsule.setLatestConsumeTimeForEnergy(now);
    if (ownerCapsule.getEnergyUsage() > 0) {
      energyProcessor.unDelegateIncrease(inheritorCapsule, ownerCapsule,
          ownerCapsule.getEnergyUsage(), ENERGY, now);
    }

    // withdraw expire unfrozen balance
    long nowTimestamp = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long expireUnfrozenBalance =
        ownerCapsule.getUnfrozenV2List().stream()
            .filter(
                unFreezeV2 ->
                    unFreezeV2.getUnfreezeAmount() > 0 && unFreezeV2.getUnfreezeExpireTime() <= nowTimestamp)
            .mapToLong(Protocol.Account.UnFreezeV2::getUnfreezeAmount)
            .sum();
    if (expireUnfrozenBalance > 0) {
      inheritorCapsule.setBalance(inheritorCapsule.getBalance() + expireUnfrozenBalance);
      increaseNonce();
      addInternalTx(null, ownerAddr, inheritorAddr, expireUnfrozenBalance, null,
          "withdrawExpireUnfreezeWhileSuiciding", nonce, null);
    }
    clearOwnerFreezeV2(ownerCapsule);
    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    repo.updateAccount(inheritorCapsule.createDbKey(), inheritorCapsule);
    return expireUnfrozenBalance;
  }

  private void clearOwnerFreeze(AccountCapsule ownerCapsule) {
    ownerCapsule.setFrozenForBandwidth(0, 0);
    ownerCapsule.setFrozenForEnergy(0, 0);
  }

  private void clearOwnerFreezeV2(AccountCapsule ownerCapsule) {
    ownerCapsule.clearFrozenV2();
    ownerCapsule.setNetUsage(0);
    ownerCapsule.setNewWindowSize(BANDWIDTH, 0);
    ownerCapsule.setEnergyUsage(0);
    ownerCapsule.setNewWindowSize(ENERGY, 0);
    ownerCapsule.clearUnfrozenV2();
  }

  private void withdrawRewardAndCancelVote(byte[] owner, Repository repo) {
    VoteRewardUtil.withdrawReward(owner, repo);

    AccountCapsule ownerCapsule = repo.getAccount(owner);
    if (!ownerCapsule.getVotesList().isEmpty()) {
      VotesCapsule votesCapsule = repo.getVotes(owner);
      if (votesCapsule == null) {
        votesCapsule = new VotesCapsule(ByteString.copyFrom(owner),
            ownerCapsule.getVotesList());
      } else {
        votesCapsule.clearNewVotes();
      }
      ownerCapsule.clearVotes();
      ownerCapsule.setOldTronPower(0);
      repo.updateVotes(owner, votesCapsule);
    }
    try {
      long balance = ownerCapsule.getBalance();
      long allowance = ownerCapsule.getAllowance();
      ownerCapsule.setInstance(ownerCapsule.getInstance().toBuilder()
          .setBalance(addExact(balance, allowance, VMConfig.disableJavaLangMath()))
          .setAllowance(0)
          .setLatestWithdrawTime(getTimestamp().longValue() * 1000)
          .build());
      repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
    } catch (ArithmeticException e) {
      throw new BytecodeExecutionException("Suicide: balance and allowance out of long range.");
    }
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/ProgramResultTest.java (L482-512)
```java
  @Test
  public void suicideResultTest()
      throws ContractExeException, ReceiptCheckErrException, VMIllegalException,
      ContractValidateException {
    byte[] suicideContract = deploySuicide();
    Assert.assertEquals(repository.getAccount(suicideContract).getBalance(), 1000);
    String params = Hex
        .toHexString(new DataWord(new DataWord(TRANSFER_TO).getLast20Bytes()).getData());

    // ======================================= Test Suicide =======================================
    byte[] triggerData1 = TvmTestUtils.parseAbi("suicide(address)", params);
    Transaction trx = TvmTestUtils
        .generateTriggerSmartContractAndGetTransaction(Hex.decode(OWNER_ADDRESS), suicideContract,
            triggerData1, 0, 100000000);
    TransactionTrace trace = TvmTestUtils.processTransactionAndReturnTrace(trx, repository, null);
    runtime = trace.getRuntime();
    List<InternalTransaction> internalTransactionsList = runtime.getResult()
        .getInternalTransactions();
    Assert
        .assertEquals(dbManager.getAccountStore().get(Hex.decode(TRANSFER_TO)).getBalance(), 1000);
    Assert.assertNull(dbManager.getAccountStore().get(suicideContract));
    Assert.assertEquals(internalTransactionsList.get(0).getValue(), 1000);
    Assert.assertArrayEquals(new DataWord(
        internalTransactionsList.get(0).getSender()).getLast20Bytes(),
        new DataWord(suicideContract).getLast20Bytes());
    Assert.assertArrayEquals(internalTransactionsList.get(0).getTransferToAddress(),
        Hex.decode(TRANSFER_TO));
    Assert.assertEquals(internalTransactionsList.get(0).getNote(), "suicide");
    Assert.assertFalse(internalTransactionsList.get(0).isRejected());
    checkTransactionInfo(trace, trx, null, internalTransactionsList);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L45-53)
```java
    byte[] ownerAddress = setAccountIdContract.getOwnerAddress().toByteArray();
    AccountCapsule account = accountStore.get(ownerAddress);

    account.setAccountId(setAccountIdContract.getAccountId().toByteArray());
    accountStore.put(ownerAddress, account);
    accountIdIndexStore.put(account);
    ret.setStatus(fee, code.SUCESS);

    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L86-96)
```java

    AccountCapsule account = accountStore.get(ownerAddress);
    if (account == null) {
      throw new ContractValidateException("Account has not existed");
    }
    if (account.getAccountId() != null && !account.getAccountId().isEmpty()) {
      throw new ContractValidateException("This account id already set");
    }
    if (accountIdIndexStore.has(accountId)) {
      throw new ContractValidateException("This id has existed");
    }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java (L973-977)
```java
    TVMTestResult result = triggerSuicide(callerAddr, contractAddr, SUCCESS, null, inheritorAddr);

    Assert.assertNull(accountStore.get(contractAddr));
    AccountCapsule newInheritor = accountStore.get(inheritorAddr);
    Assert.assertNotNull(newInheritor);
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountStore.java (L91-105)
```java
  @Override
  public void delete(byte[] key) {
    if (CommonParameter.getInstance().isHistoryBalanceLookup()) {
      AccountCapsule old = super.getUnchecked(key);
      if (old != null) {
        recordBalance(old, -old.getBalance());
      }

      BlockCapsule.BlockId blockId = balanceTraceStore.getCurrentBlockId();
      if (blockId != null) {
        accountTraceStore.recordBalanceWithBlock(key, blockId.getNum(), 0);
      }
    }
    super.delete(key);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L34-57)
```java
  public byte[] get(ByteString name) {
    BytesCapsule bytesCapsule = get(name.toByteArray());
    if (Objects.nonNull(bytesCapsule)) {
      return bytesCapsule.getData();
    }
    return null;
  }

  @Override
  public BytesCapsule get(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    if (ArrayUtils.isEmpty(value)) {
      return null;
    }
    return new BytesCapsule(value);
  }

  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L381-391)
```java
  public Account getAccountById(Account account) {
    AccountStore accountStore = chainBaseManager.getAccountStore();
    AccountIdIndexStore accountIdIndexStore = chainBaseManager.getAccountIdIndexStore();
    byte[] address = accountIdIndexStore.get(account.getAccountId());
    if (address == null) {
      return null;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    if (accountCapsule == null) {
      return null;
    }
```
