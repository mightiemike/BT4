### Title
Unbounded Delegation-Index List Causes Gas/CPU DoS on Freeze/Unfreeze Transactions - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
`DelegatedResourceAccountIndexStore.convert(byte[] address)` iterates over the full `toAccountsList`/`fromAccountsList` of an account's `DelegatedResourceAccountIndexCapsule`, performing a DB write (`this.delegate(...)`) for every entry. These lists are grown, one entry at a time, by ordinary `FreezeBalanceContract` (old resource-delegation) transactions with no upper bound on the number of distinct addresses an account can delegate resources to/from. Any subsequent freeze/unfreeze transaction touching that account re-triggers a full linear scan (and equivalent number of DB puts) of this list, mirroring the `GTL.sol` `_withdrawalQueue` DoS pattern: an attacker can cheaply inflate a queue/list that a later transaction must iterate in full.

### Finding Description
`FreezeBalanceActuator.delegateResource()` maintains a per-account index of delegation partners. When `supportAllowDelegateOptimization()` is disabled, each new delegate target is appended via `ownerIndexCapsule.addToAccount(...)` / `receiverIndexCapsule.addFromAccount(...)` with only a "contains" de-duplication check ( [1](#0-0) ), and no cap on the number of distinct addresses.

When `supportAllowDelegateOptimization()` is enabled, the same code path instead calls `delegatedResourceAccountIndexStore.convert(ownerAddress)` / `convert(receiverAddress)` before every new delegation ( [2](#0-1) ). `UnfreezeBalanceActuator.execute()` calls the same `convert()` on both owner and receiver whenever a delegated resource is fully unfrozen ( [3](#0-2) ).

`convert()` itself is a straight, unbounded loop over the legacy index lists: [4](#0-3) 

For each entry in `toList`/`fromList` it performs a full `delegate()` call, which itself does two DB `put()` operations ( [5](#0-4) ). Because the legacy list-append path (`addToAccount`/`addFromAccount`) has no maximum-size validation anywhere in `FreezeBalanceActuator.validate()` ( [6](#0-5) ), an attacker can cheaply grow this list to an arbitrarily large size by repeatedly freezing the minimum amount (1 TRX, `frozenBalance >= TRX_PRECISION`) and delegating to a large number of freshly created receiver addresses, each becoming a unique entry.

Once that account is touched again by any freeze/unfreeze transaction (from the attacker or, in the receiver case, from anyone delegating to/from that account) after `AllowDelegateOptimization` activation, `convert()` performs O(n) DB reads/writes for that single transaction — an unbounded loop over an attacker-controlled data structure, directly analogous to the reported `_withdrawalQueue` DoS in `GTL.sol` where `cancelWithdrawal`/`processWithdrawals` iterate the full queue.

### Impact Explanation
A malicious actor can inflate their own account's (or, by delegating to a victim, the victim's) `toAccountsList`/`fromAccountsList` to a very large size at low cost (minimum freeze amount is 1 TRX per new entry, and unfreeze can later reclaim the frozen TRX, making the attack close to free besides bandwidth/energy cost of many small transactions). Any later transaction that triggers `convert()` on that address (a subsequent freeze/unfreeze delegating resource) then performs a correspondingly large number of DB writes within a single transaction execution, which can push that transaction toward the block's energy/time budget, degrading node performance for whoever interacts with the inflated account and potentially disrupting normal delegation operations against it (localized DoS), consistent in class with the reported issue (unbounded loop over user-controlled data reachable from unprivileged transactions).

### Likelihood Explanation
Reaching this code path requires only sending many low-cost `FreezeBalanceContract` transactions with distinct receiver addresses before `AllowDelegateOptimization` is enabled (or, if it is already enabled, an attacker could still delegate to many distinct never-before-used receivers using the new `DelegateResourceContract` flow to grow `V2` indexes, though those use `delegate()`/`delegateV2()` DB-keyed entries rather than a single growing list — the specific unbounded in-list iteration is confined to the legacy `convert()` migration path). Exploitation therefore depends on network state (whether `AllowDelegateOptimization` was toggled on with a pre-existing, attacker-inflated legacy index), which is a committee-controlled parameter and reduces but does not eliminate practical likelihood, since the migration path exists specifically to handle already-large legacy lists.

### Recommendation
- Cap the number of distinct delegation partners tracked in `DelegatedResourceAccountIndexCapsule.toAccountsList`/`fromAccountsList`, enforced in `FreezeBalanceActuator.validate()`/`delegateResource()`, analogous to the `UNFREEZE_MAX_TIMES` limit already used for `UnfreezeBalanceV2Actuator`.
- In `DelegatedResourceAccountIndexStore.convert()`, process the migration incrementally (e.g., migrate a bounded number of entries per call, or lazily migrate on read) instead of iterating the entire list synchronously inside a single transaction's execution.
- Consider replacing the list-based index with a directly keyed store (as already done for the `V2` delegate/undelegate paths) so lookups and mutations do not require iterating an unbounded collection.

### Proof of Concept
1. Attacker account `A`, with `AllowDelegateOptimization` not yet enabled, repeatedly sends `FreezeBalanceContract` transactions with `frozenBalance = 1 TRX` and a fresh `receiverAddress` each time (`R1, R2, …, Rn`), each accepted by `FreezeBalanceActuator.validate()`/`execute()` because there is no bound on distinct receivers.
2. Each transaction appends one entry to `A`'s `toAccountsList` via `ownerIndexCapsule.addToAccount(...)` ( [7](#0-6) ), growing the list to size `n` with no upper limit.
3. Once the committee enables `AllowDelegateOptimization`, the next `FreezeBalanceContract`/`UnfreezeBalanceContract` transaction touching account `A` invokes `delegatedResourceAccountIndexStore.convert(A)`, which loops over all `n` entries and performs `n` `delegate()` calls (2n DB puts) ( [4](#0-3) ), causing that single transaction to perform O(n) work proportional to attacker-chosen `n`.
4. Repeating this with larger `n` scales the per-transaction DB work linearly, without any protocol-level limit rejecting the growth in step 1.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L152-277)
```java
  @Override
  public boolean validate() throws ContractValidateException {
    if (this.any == null) {
      throw new ContractValidateException(ActuatorConstant.CONTRACT_NOT_EXIST);
    }
    if (chainBaseManager == null) {
      throw new ContractValidateException(ActuatorConstant.STORE_NOT_EXIST);
    }
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    if (!any.is(FreezeBalanceContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [FreezeBalanceContract],real type[" + any
              .getClass() + "]");
    }

    final FreezeBalanceContract freezeBalanceContract;
    try {
      freezeBalanceContract = this.any.unpack(FreezeBalanceContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    byte[] ownerAddress = freezeBalanceContract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }

    long frozenBalance = freezeBalanceContract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    int frozenCount = accountCapsule.getFrozenCount();
    if (!(frozenCount == 0 || frozenCount == 1)) {
      throw new ContractValidateException("frozenCount must be 0 or 1");
    }
    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }

    long frozenDuration = freezeBalanceContract.getFrozenDuration();
    long minFrozenTime = dynamicStore.getMinFrozenTime();
    long maxFrozenTime = dynamicStore.getMaxFrozenTime();

    boolean needCheckFrozeTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1;//for test
    if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime
        && frozenDuration <= maxFrozenTime)) {
      throw new ContractValidateException(
          "frozenDuration must be less than " + maxFrozenTime + " days "
              + "and more than " + minFrozenTime + " days");
    }

    switch (freezeBalanceContract.getResource()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      case TRON_POWER:
        if (dynamicStore.supportAllowNewResourceModel()) {
          byte[] receiverAddress = freezeBalanceContract.getReceiverAddress().toByteArray();
          if (!ArrayUtils.isEmpty(receiverAddress)) {
            throw new ContractValidateException(
                "TRON_POWER is not allowed to delegate to other accounts.");
          }
        } else {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
        break;
      default:
        if (dynamicStore.supportAllowNewResourceModel()) {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY、TRON_POWER]");
        } else {
          throw new ContractValidateException(
              "ResourceCode error, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
    }

    //todo：need version control and config for delegating resource
    byte[] receiverAddress = freezeBalanceContract.getReceiverAddress().toByteArray();
    //If the receiver is included in the contract, the receiver will receive the resource.
    if (!ArrayUtils.isEmpty(receiverAddress) && dynamicStore.supportDR()) {
      if (Arrays.equals(receiverAddress, ownerAddress)) {
        throw new ContractValidateException("receiverAddress must not be the same as ownerAddress");
      }

      if (!DecodeUtil.addressValid(receiverAddress)) {
        throw new ContractValidateException("Invalid receiverAddress");
      }

      AccountCapsule receiverCapsule = accountStore.get(receiverAddress);
      if (receiverCapsule == null) {
        String readableOwnerAddress = StringUtil.createReadableString(receiverAddress);
        throw new ContractValidateException(
            ActuatorConstant.ACCOUNT_EXCEPTION_STR
                + readableOwnerAddress + NOT_EXIST_STR);
      }

      if (dynamicStore.getAllowTvmConstantinople() == 1
          && receiverCapsule.getType() == AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");

      }

    }

    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L319-345)
```java
    //modify DelegatedResourceAccountIndexStore
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L183-188)
```java
        } else {
          //modify DelegatedResourceAccountIndexStore new
          delegatedResourceAccountIndexStore.convert(ownerAddress);
          delegatedResourceAccountIndexStore.convert(receiverAddress);
          delegatedResourceAccountIndexStore.unDelegate(ownerAddress, receiverAddress);
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

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L63-75)
```java
  public void delegate(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```
