### Title
Unmetered O(n) legacy-index migration in `DelegatedResourceAccountIndexStore.convert()` allows cheap fixed-fee transactions to trigger unbounded DB work - ([File: chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java])

### Finding Description
When `supportAllowDelegateOptimization()` is `false`, `FreezeBalanceActuator.delegateResource()` appends the receiver/owner to an unbounded, uncapped `toAccountsList`/`fromAccountsList` inside `DelegatedResourceAccountIndexCapsule` [1](#0-0) . There is no on-chain cap on the size of this list — no `MAX_DELEGATE`-style limit was found in the store, capsule, or actuator validation.

Once the fork parameter `ALLOW_DELEGATE_OPTIMIZATION` flips to `1` (a one-time, non-reversible committee proposal per `ProposalUtil` which only allows `value == 1`, and `ProposalService.process` calling `saveAllowDelegateOptimization`) [2](#0-1) [3](#0-2) , every subsequent `FreezeBalanceActuator`/`UnfreezeBalanceActuator`/`DelegateResourceActuator` call routes through `DelegatedResourceAccountIndexStore.convert(address)` for both the owner and receiver address before performing the delegation [4](#0-3) .

`convert(address)` iterates the **entire legacy** `toAccountsList` and `fromAccountsList` for that address and re-inserts every entry as two new key/value V2 DB records via `delegate(...)`, i.e. `O(n)` `put()` calls (2n writes) for a list of size n, then deletes the old record [5](#0-4) . This work happens synchronously inside `execute()` of a single transaction.

Both `FreezeBalanceActuator.calcFee()` and `DelegateResourceActuator.calcFee()` return a fixed `0` [6](#0-5) [7](#0-6) , meaning bandwidth/energy billed for the triggering transaction is based only on transaction byte size, not on the size of the legacy list being migrated. The existing test `testMultiFreezeDelegatedBalanceForBandwidth` already demonstrates that a single `FreezeBalanceActuator.execute()` call migrates an entire pre-populated `toAccountsList` (100 entries in the test) in one shot [8](#0-7) , confirming the mechanism scales with list size regardless of fee.

Critically, `convert()` is invoked for **both** the owner address and the receiver address supplied in the contract, and the receiver address is fully attacker-chosen and requires no consent/signature from that account. This means an attacker does not need to build their own oversized legacy list — they can target any pre-existing account (e.g., an exchange or whale with a large pre-fork delegation graph) as the "receiver" in a minimal `FreezeBalanceContract`, forcing that victim account's entire legacy index to be migrated inside the attacker's cheap transaction.

No existing check (`validate()`, `ForkController`, fee calculation, or bandwidth/energy metering) accounts for the size of the legacy list before charging or bounding the work.

### Impact Explanation
This is a DoS-via-protocol-implementation issue: a fixed, near-zero-fee transaction can force `O(n)` LevelDB/RocksDB writes and CPU work proportional to an arbitrary pre-existing legacy list size, with no corresponding energy/bandwidth charge (`calcFee()==0`, fee independent of n). If n is large (tens/hundreds of thousands of entries accumulated by any account before the fork), the single triggering transaction can consume disproportionate block-processing time relative to what was paid, violating faithful metering and potentially slowing block production/verification for that block. This falls in the "DoS via the TRON protocol implementation" bounty class.

### Likelihood Explanation
- The fork gate (`ALLOW_DELEGATE_OPTIMIZATION`) transitions only once (0→1, cannot be reverted per `ProposalUtil` validation), so this is a one-time migration burst per address, not a repeatable unbounded attack after the address is converted.
- Self-targeting (attacker inflates their own list) requires the attacker to have funded and broadcast n prior transactions before the fork — real but non-trivial cost/time investment, bounded only by the attacker's own resources and available bandwidth/energy over time.
- Targeting a third-party ("receiver") with a pre-existing large legacy list requires no attacker cost beyond a single minimal transaction, and no consent from the victim, making that path essentially free to trigger for any account whose legacy list happens to be large going into the fork activation window.
- Feasibility depends on the fork-activation timing and existence of accounts with large legacy lists at that moment — a narrow but real window around the one-time hard-fork activation.

### Recommendation
- Bound the size of `toAccountsList`/`fromAccountsList` at write time (cap the number of distinct delegate relationships per account) in `FreezeBalanceActuator`/`UnfreezeBalanceActuator` prior to the optimization fork.
- Make `convert()` incremental/paginated (e.g., migrate a bounded number of entries per call, resuming across multiple transactions/blocks) instead of migrating the full list synchronously in one `execute()`.
- Charge a fee or additional energy/bandwidth cost proportional to the number of entries migrated during `convert()`, so the invariant "fee reflects computational work" holds.
- Alternatively, perform the legacy-to-V2 migration as a batched, off-transaction background/genesis-style migration triggered at fork activation rather than lazily inside arbitrary user transactions.

### Proof of Concept
Extend `DelegatedResourceAccountIndexStoreTest`/`FreezeBalanceActuatorTest` similar to the existing `testMultiFreezeDelegatedBalanceForBandwidth` but scaled to a large n (e.g., 100,000) and measure wall time:
```java
@Test
public void testConvertScalesWithLegacyListSize() {
  DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
      new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(OWNER_ADDRESS_BYTES));
  int N = 100_000;
  for (int i = 0; i < N; i++) {
    ownerIndexCapsule.addToAccount(ByteString.copyFrom(randomAddress()));
  }
  delegatedResourceAccountIndexStore.put(OWNER_ADDRESS_BYTES, ownerIndexCapsule);

  long start = System.nanoTime();
  delegatedResourceAccountIndexStore.convert(OWNER_ADDRESS_BYTES); // triggered by a single cheap FreezeBalance/DelegateResource tx
  long elapsed = System.nanoTime() - start;

  // elapsed scales ~linearly with N while calcFee() for the triggering
  // FreezeBalanceActuator/DelegateResourceActuator remains 0 regardless of N.
  Assert.assertTrue(elapsed > /* threshold proving O(n) cost */ 0);
}
```
Expected result: execution time and DB write count grow linearly with the pre-populated legacy list size, while the actuator's `calcFee()` remains `0` for any N, confirming the fee charged does not reflect the work performed.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L284-287)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L320-345)
```java
    if (!dynamicPropertiesStore.supportAllowDelegateOptimization()) {

      DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
          delegatedResourceAccountIndexStore.get(ownerAddress);
      if (ownerIndexCapsule == null) {
        ownerIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(ownerAddress));
      }
      List<ByteString> toAccountsList = ownerIndexCapsule.getToAccountsList();
      if (!toAccountsList.contains(ByteString.copyFrom(receiverAddress))) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(receiverAddress));
      }
      delegatedResourceAccountIndexStore.put(ownerAddress, ownerIndexCapsule);

      DelegatedResourceAccountIndexCapsule receiverIndexCapsule
          = delegatedResourceAccountIndexStore.get(receiverAddress);
      if (receiverIndexCapsule == null) {
        receiverIndexCapsule = new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(receiverAddress));
      }
      List<ByteString> fromAccountsList = receiverIndexCapsule
          .getFromAccountsList();
      if (!fromAccountsList.contains(ByteString.copyFrom(ownerAddress))) {
        receiverIndexCapsule.addFromAccount(ByteString.copyFrom(ownerAddress));
      }
      delegatedResourceAccountIndexStore.put(receiverAddress, receiverIndexCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L347-353)
```java
    } else {
      // modify DelegatedResourceAccountIndexStore new
      delegatedResourceAccountIndexStore.convert(ownerAddress);
      delegatedResourceAccountIndexStore.convert(receiverAddress);
      delegatedResourceAccountIndexStore.delegate(ownerAddress, receiverAddress,
          dynamicPropertiesStore.getLatestBlockHeaderTimestamp());
    }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L598-608)
```java
      case ALLOW_DELEGATE_OPTIMIZATION: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_6)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_DELEGATE_OPTIMIZATION]");
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_DELEGATE_OPTIMIZATION] is only allowed to be 1");
        }
        break;
      }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L318-321)
```java
        case ALLOW_DELEGATE_OPTIMIZATION: {
          manager.getDynamicPropertiesStore().saveAllowDelegateOptimization(entry.getValue());
          break;
        }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L42-61)
```java
  public void convert(byte[] address) {
    DelegatedResourceAccountIndexCapsule indexCapsule = this.get(address);
    if (indexCapsule == null) {
      // convert complete or have no delegate
      return;
    }
    // convert old data
    List<ByteString> toList = indexCapsule.getToAccountsList();
    for (int i = 0; i < toList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(address, toList.get(i).toByteArray(), i + 1L);
    }

    List<ByteString> fromList = indexCapsule.getFromAccountsList();
    for (int i = 0; i < fromList.size(); i++) {
      // use index as the timestamp, just to keep index in order
      this.delegate(fromList.get(i).toByteArray(), address, i + 1L);
    }
    this.delete(address);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L278-280)
```java
  public long calcFee() {
    return 0;
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/FreezeBalanceActuatorTest.java (L275-331)
```java
  @Test
  public void testMultiFreezeDelegatedBalanceForBandwidth() {
    dbManager.getDynamicPropertiesStore().saveAllowDelegateResource(1);
    dbManager.getDynamicPropertiesStore().saveAllowDelegateOptimization(1L);
    dbManager.getDynamicPropertiesStore().saveLatestBlockHeaderTimestamp(10000L);
    long frozenBalance = 1_000_000_000L;
    long duration = 3;
    final int RECEIVE_COUNT = 100;
    String[] RECEIVE_ADDRESSES = new String[RECEIVE_COUNT + 1];

    DelegatedResourceAccountIndexCapsule ownerIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(
            ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS)));
    for (int i = 0; i < RECEIVE_COUNT + 1; i++) {
      ECKey ecKey = new ECKey(Utils.getRandom());
      RECEIVE_ADDRESSES[i] = ByteArray.toHexString(ecKey.getAddress());
      if (i != RECEIVE_COUNT) {
        ownerIndexCapsule.addToAccount(ByteString.copyFrom(ecKey.getAddress()));
      }
    }
    dbManager.getDelegatedResourceAccountIndexStore().put(
        ByteArray.fromHexString(OWNER_ADDRESS), ownerIndexCapsule);
    AccountCapsule receiverCapsule =
        new AccountCapsule(
            ByteString.copyFromUtf8("receiver"),
            ByteString.copyFrom(ByteArray.fromHexString(RECEIVE_ADDRESSES[RECEIVE_COUNT])),
            AccountType.Normal,
            initBalance);
    dbManager.getAccountStore().put(receiverCapsule.getAddress().toByteArray(), receiverCapsule);

    TransactionResultCapsule ret = new TransactionResultCapsule();
    FreezeBalanceActuator actuator = new FreezeBalanceActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getDelegatedContractForBandwidth(
            OWNER_ADDRESS, RECEIVE_ADDRESSES[RECEIVE_COUNT], frozenBalance, duration));
    try {
      ownerIndexCapsule = dbManager
          .getDelegatedResourceAccountIndexStore().getIndex(ByteArray.fromHexString(OWNER_ADDRESS));
      List<ByteString> beforeList = ownerIndexCapsule.getToAccountsList();
      actuator.validate();
      actuator.execute(ret);

      //check DelegatedResourceAccountIndex convert
      ownerIndexCapsule = dbManager
          .getDelegatedResourceAccountIndexStore().get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertNull(ownerIndexCapsule);

      ownerIndexCapsule = dbManager
          .getDelegatedResourceAccountIndexStore().getIndex(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(0, ownerIndexCapsule.getFromAccountsList().size());
      List<ByteString> tmpList = ownerIndexCapsule.getToAccountsList();
      Assert.assertEquals(RECEIVE_COUNT + 1, ownerIndexCapsule.getToAccountsList().size());
      for (int i = 0; i < RECEIVE_COUNT; i++) {
        Assert.assertEquals(beforeList.get(i), tmpList.get(i));
      }
      Assert.assertEquals(RECEIVE_ADDRESSES[RECEIVE_COUNT],
          ByteArray.toHexString(tmpList.get(RECEIVE_COUNT).toByteArray()));
```
