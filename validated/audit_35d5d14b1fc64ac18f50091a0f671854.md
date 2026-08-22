### Title
Unbounded DelegatedResourceAccountIndex Growth Enables RPC-API DoS via `getV2Index` prefix scans - (File: `chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java`)

### Summary
The reported Alchemix `MAX_DELEGATES` bug is about an unbounded delegate list that becomes too expensive to iterate during a state-changing operation. Java-tron has an analogous unbounded-growth structure in its `DelegatedResourceAccountIndexStore`: the V2 delegation index is stored as one DB key per `(owner, receiver)` pair with **no cap** on how many distinct pairs a single address can accumulate [1](#0-0) . Reading that index (`getV2Index`) performs an unbounded `prefixQuery` scan over all matching keys, exposing an attacker-controlled cost to an anonymous RPC/HTTP endpoint.

### Finding Description
`DelegateResourceActuator`/`DelegateResourceProcessor` allow any account to delegate as little as 1 TRX of frozen resource to any receiver [2](#0-1) . Each delegation call writes a brand-new pair of keys into `DelegatedResourceAccountIndexStore` using `V2_FROM_PREFIX`/`V2_TO_PREFIX` concatenated with the address pair [3](#0-2) , with no limit analogous to `MAX_DELEGATES` on how many distinct owner/receiver pairs a single address can be associated with.

To read this data back, `getV2Index` calls `getWithPrefix`, which performs two full `prefixQuery` range scans (one for "from" keys, one for "to" keys), then materializes, sorts, and streams all matching entries into a response object [4](#0-3) . This function is invoked from `Wallet.getDelegatedResourceAccountIndexV2`, which is reachable from multiple anonymous, unauthenticated entry points: the gRPC `RpcApiService`, the HTTP `GetDelegatedResourceAccountIndexV2Servlet` [5](#0-4) , and the PBFT/Solidity node variants (`GetDelegatedResourceAccountIndexV2OnPBFTServlet`, `GetDelegatedResourceAccountIndexV2OnSolidityServlet`).

Because the number of `(owner, receiver)` index entries for a single victim address is unbounded (no `MAX_DELEGATES`-style cap exists on the V2 store, unlike the legacy V1 store which at least deduplicated into a single growing list per address [6](#0-5) ), an attacker can cheaply create many funded accounts (minimum 1 TRX each) and issue `DelegateResourceContract` transactions that all target one victim address as `receiverAddress` (or as `ownerAddress`). This inflates the number of DB keys matching that address's prefix. The `RateLimiterServlet` base class only throttles request *rate*, not per-request computational cost, so it does not mitigate a single expensive prefix scan.

### Impact Explanation
Every subsequent call to `GetDelegatedResourceAccountIndexV2` for the poisoned address forces the serving node to perform an unbounded RocksDB/LevelDB range scan, deserialize each `DelegatedResourceAccountIndexCapsule`, sort by timestamp, and build/stream a potentially very large protobuf response. A sufficiently large number of delegation records (achievable cheaply since each entry costs only 1 TRX plus normal bandwidth/energy fees) can make this query take excessive time and memory, degrading or denying availability of the API-serving node for that request and potentially for other concurrent requests sharing the same DB/thread pool. This is a Denial-of-Service condition reachable via anonymous RPC/HTTP requests, matching the "DoS via RPC-API or protocol implementation" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate: the attack requires funding many accounts and paying normal transaction fees for delegation, so it is not free, but the per-record cost (1 TRX minimum, standard bandwidth/energy fee) is low relative to the amplification achieved on the read path, and no validation in `DelegateResourceActuator.validate()` limits the number of distinct receivers/owners an address can accumulate [7](#0-6) .

### Recommendation
1. Introduce a cap (analogous to `MAX_DELEGATES`) on the number of distinct `(owner, receiver)` delegation pairs tracked per address, enforced in `DelegateResourceActuator.validate()` / `DelegateResourceProcessor`.
2. Bound or paginate `getWithPrefix`/`getV2Index` so a single RPC/HTTP query cannot trigger an unbounded range scan; add a configurable maximum result size and/or streaming with an upper limit.
3. Consider charging a bandwidth/energy cost proportional to the size of the index being scanned when the query API is invoked, similar to metering used elsewhere for store iteration.

### Proof of Concept
Conceptual PoC (not executed):
1. Create N (e.g., 5,000–10,000) funded accounts, each holding slightly more than 1 TRX.
2. From each account, freeze the minimum bandwidth/energy amount and broadcast a `DelegateResourceContract` naming a single victim address as `receiverAddress` (per `DelegateResourceActuator` validate/execute logic) [8](#0-7) .
3. Query `GetDelegatedResourceAccountIndexV2` (HTTP or gRPC) for the victim address and measure response latency/resource usage growth as N increases, verifying the unbounded `prefixQuery` cost in `getWithPrefix` [9](#0-8) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L77-89)
```java
  public void delegateV2(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(V2_FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(V2_TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L114-137)
```java
  public DelegatedResourceAccountIndexCapsule getV2Index(byte[] address) {
    return getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address);
  }

  private DelegatedResourceAccountIndexCapsule getWithPrefix(byte[] fromPrefix, byte[] toPrefix, byte[] address) {
    DelegatedResourceAccountIndexCapsule tmpIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(address));

    byte[] key = Bytes.concat(fromPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpToList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    List<ByteString> list = tmpToList.stream()
        .map(DelegatedResourceAccountIndexCapsule::getAccount).collect(Collectors.toList());
    tmpIndexCapsule.setAllToAccounts(list);

    key = Bytes.concat(toPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpFromList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpFromList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    list = tmpFromList.stream().map(DelegatedResourceAccountIndexCapsule::getAccount).collect(
        Collectors.toList());
    tmpIndexCapsule.setAllFromAccounts(list);
    return tmpIndexCapsule;
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L44-98)
```java
  @Override
  public boolean execute(Object result) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) result;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    final DelegateResourceContract delegateResourceContract;
    AccountStore accountStore = chainBaseManager.getAccountStore();
    byte[] ownerAddress;
    try {
      delegateResourceContract = this.any.unpack(DelegateResourceContract.class);
      ownerAddress = getOwnerAddress().toByteArray();
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }

    AccountCapsule ownerCapsule = accountStore
        .get(delegateResourceContract.getOwnerAddress().toByteArray());
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long delegateBalance = delegateResourceContract.getBalance();
    boolean lock = delegateResourceContract.getLock();
    long lockPeriod = getLockPeriod(dynamicStore.supportMaxDelegateLockPeriod(),
            delegateResourceContract);
    byte[] receiverAddress = delegateResourceContract.getReceiverAddress().toByteArray();

    // delegate resource to receiver
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, lock, lockPeriod);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    accountStore.put(ownerCapsule.createDbKey(), ownerCapsule);

    ret.setStatus(fee, code.SUCESS);

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L101-126)
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
    DelegatedResourceStore delegatedResourceStore = chainBaseManager.getDelegatedResourceStore();
    if (!any.is(DelegateResourceContract.class)) {
      throw new ContractValidateException(
          "contract type error,expected type [DelegateResourceContract],real type["
              + any.getClass() + "]");
    }

    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support Delegate resource transaction,"
          + " need to be opened by the committee");
    }

```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L147-150)
```java
    long delegateBalance = delegateResourceContract.getBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L166-180)
```java
    //modify DelegatedResourceAccountIndex
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    byte[] fromKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_FROM_PREFIX(), ownerAddress, receiverAddress);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(receiverAddress));
    toIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(
        DelegatedResourceAccountIndexStore.getV2_TO_PREFIX(), receiverAddress, ownerAddress);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(ownerAddress));
    fromIndexCapsule.setTimestamp(now);
    repo.updateDelegatedResourceAccountIndex(toKey, fromIndexCapsule);
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L26-69)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String address = request.getParameter(VALUE_FIELD_NAME);
      if (visible) {
        address = Util.getHexAddress(address);
      }
      fillResponse(ByteString.copyFrom(ByteArray.fromHexString(address)), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      boolean visible = params.isVisible();
      String input = params.getParams();
      if (visible) {
        JSONObject jsonObject = JSONObject.parseObject(input);
        String value = jsonObject.getString(VALUE_FIELD_NAME);
        jsonObject.put(VALUE_FIELD_NAME, Util.getHexAddress(value));
        input = jsonObject.toJSONString();
      }

      BytesMessage.Builder build = BytesMessage.newBuilder();
      JsonFormat.merge(input, build, visible);

      fillResponse(build.getValue(), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
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
