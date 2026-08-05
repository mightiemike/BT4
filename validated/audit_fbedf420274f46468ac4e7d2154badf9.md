### Title
FreezeBalanceContract with `receiver_address` causes JSON-RPC `getTo()`/`getTransactionAmount()` to report a spoofed value-transfer `{to, value}` tuple that does not correspond to any actual balance change of the receiver - ([File: framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java])

### Summary
`JsonRpcApiUtil.getTo()` maps `FreezeBalanceContract.receiver_address` (a resource-delegation target, not a value recipient) directly into the Ethereum-compatible `to` field, while `getTransactionAmount()` independently maps `FreezeBalanceContract.frozen_balance` into the `value` field. An unprivileged attacker can freeze their own TRX and set `receiver_address` to any existing, arbitrary account (e.g. a known exchange/bridge deposit address), producing an `eth_getBlockByHash`/`eth_getTransactionByHash` result that looks exactly like a native-currency transfer of `frozen_balance` TRX to that address, even though the receiver's balance never changes and the frozen TRX stays fully under the attacker's control (recoverable via unfreeze).

### Finding Description
`JsonRpcApiUtil.getTo()` handles `ContractType.FreezeBalanceContract` by unpacking the contract and, if `receiver_address` is non-empty, adding it as the "to" address: [1](#0-0) 

Independently, `getTransactionAmount()` computes the `value` field for `FreezeBalanceContract` as the raw `frozen_balance`: [2](#0-1) 

These two independently-computed fields are combined in `TransactionResult`, which is the object serialized for `eth_getBlockByHash`/`eth_getTransactionByHash`: [3](#0-2) 

The actual on-chain effect of `FreezeBalanceContract`, per `FreezeBalanceActuator.execute()`, is that the **owner's** balance decreases by `frozen_balance` (the TRX becomes frozen but remains owned by/returnable to the owner), and if a valid `receiver_address` is set and delegated-resource support is enabled, the receiver only gains bandwidth/energy **resource weight** — never any TRX balance increase: [4](#0-3) [5](#0-4) 

`validate()` only requires that `receiver_address` be a valid, existing, non-owner account — it does not require the receiver's consent, and does not tie the "value" reported to any actual transfer to that account: [6](#0-5) 

So an attacker can craft a `FreezeBalanceContract{owner_address=attacker, receiver_address=<any existing account, e.g. exchange hot wallet>, frozen_balance=X, resource=BANDWIDTH}`, broadcast it, and after it's mined query `eth_getBlockByHash`/`eth_getTransactionByHash`. The JSON-RPC result will show `from=attacker`, `to=<victim/exchange address>`, `value=X`, exactly mimicking a plain native-currency transfer of X TRX into that address — while in reality the exchange/victim's balance is completely untouched, and the frozen TRX remains recoverable by the attacker via `UnfreezeBalanceContract`. Nothing in `getTo()`/`getTransactionAmount()` checks whether the address in question actually experienced a balance increase; the two fields are populated purely from different, semantically-unrelated sub-fields of the contract (a resource-delegation target vs. a frozen amount) that happen to be reused for the Ethereum `{to, value}` compatibility shim.

### Impact Explanation
Any external system that consumes java-tron's Ethereum-compatible JSON-RPC (`eth_getBlockByHash`, `eth_getTransactionByHash`) to detect "deposits" by matching `to == known_address && value > 0` — a common, simple integration pattern for bridges/exchanges providing EVM-compatible tooling — can be tricked into crediting a deposit that never happened. The attacker never loses custody of the frozen TRX (only bandwidth/energy delegation is given away, and even that can be reversed via `UnDelegateResourceContract`/unfreeze), so this is a classic fake-deposit/value-duplication vector against downstream integrators relying on the RPC's Ethereum-shaped output as a source of truth for value transfers.

### Likelihood Explanation
Fully reachable by an unprivileged attacker: `FreezeBalanceContract` is a normal, permissionless transaction type; the only precondition is that `receiver_address` already exists as an account on-chain (trivially true for any exchange/bridge deposit address, since those addresses must exist to receive normal deposits). No special permissions, admin action, or leaked keys are required, and the attack is fully repeatable for arbitrary amounts and targets.

### Recommendation
Do not conflate resource-delegation counterparties (`FreezeBalanceContract.receiver_address`, `DelegateResourceContract.receiver_address`, `VoteWitnessContract` vote addresses) with Ethereum-style value-transfer recipients in `JsonRpcApiUtil.getTo()`/`getTransactionAmount()`. Either: (1) omit/zero the `to`/`value` fields for contract types that do not perform a genuine balance-increasing transfer to that address, or (2) expose contract-type metadata (e.g., via `input`/receipt logs) so downstream consumers can distinguish "resource delegation target" from "value recipient," rather than presenting both through the same generic `{to, value}} tuple used for `TransferContract`.

### Proof of Concept
```java
// Pseudo-integration test, framework module
@Test
public void freezeBalanceSpoofsToAndValueForArbitraryReceiver() throws Exception {
  // 1. Setup: attacker account with balance, victim/"exchange" account already exists.
  AccountCapsule attacker = new AccountCapsule(ByteString.copyFromUtf8("attacker"),
      ByteString.copyFrom(ATTACKER_ADDR), AccountType.Normal, 10_000_000_000L);
  AccountCapsule victim = new AccountCapsule(ByteString.copyFromUtf8("victim"),
      ByteString.copyFrom(VICTIM_ADDR), AccountType.Normal, 0L);
  dbManager.getAccountStore().put(attacker.createDbKey(), attacker);
  dbManager.getAccountStore().put(victim.createDbKey(), victim);

  long frozenAmount = 1_000_000_000L; // 1000 TRX
  FreezeBalanceContract contract = FreezeBalanceContract.newBuilder()
      .setOwnerAddress(ByteString.copyFrom(ATTACKER_ADDR))
      .setReceiverAddress(ByteString.copyFrom(VICTIM_ADDR))
      .setFrozenBalance(frozenAmount)
      .setFrozenDuration(3)
      .setResource(ResourceCode.BANDWIDTH)
      .build();

  FreezeBalanceActuator actuator = new FreezeBalanceActuator();
  actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(Any.pack(contract));
  TransactionResultCapsule ret = new TransactionResultCapsule();
  actuator.validate();
  actuator.execute(ret);

  // 2. Assert real on-chain effect: victim's TRX balance is UNCHANGED.
  long victimBalanceAfter = dbManager.getAccountStore().get(VICTIM_ADDR).getBalance();
  Assert.assertEquals(0L, victimBalanceAfter); // no actual value transfer occurred

  // 3. But the JSON-RPC compatibility layer reports a spoofed transfer:
  Transaction.Contract txContract = Transaction.Contract.newBuilder()
      .setType(ContractType.FreezeBalanceContract)
      .setParameter(Any.pack(contract))
      .build();
  byte[] toAddr = JsonRpcApiUtil.getToAddress(
      Transaction.newBuilder()
          .setRawData(Transaction.raw.newBuilder().addContract(txContract))
          .build());
  long reportedValue = JsonRpcApiUtil.getTransactionAmount(txContract, "dummyhash", wallet);

  // Invariant violated: reported "to"/"value" look like a genuine transfer to VICTIM_ADDR,
  // but VICTIM_ADDR received nothing.
  Assert.assertArrayEquals(VICTIM_ADDR, toAddr);
  Assert.assertEquals(frozenAmount, reportedValue);
  Assert.assertEquals(0L, victimBalanceAfter); // proves to/value do not reflect real beneficiary
}
```
Expected result: the test demonstrates that `getToAddress`/`getTransactionAmount` produce a `{to=VICTIM_ADDR, value=frozenAmount}` pair identical in shape to a genuine transfer, while the victim account's on-chain balance is provably unaffected — confirming the invariant "`to` must reflect the true beneficiary of value transfer" is violated for `FreezeBalanceContract`.

### Citations

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L160-166)
```java
        case FreezeBalanceContract:
          ByteString receiverAddress = contractParameter.unpack(FreezeBalanceContract.class)
              .getReceiverAddress();
          if (!receiverAddress.isEmpty()) {
            list.add(receiverAddress);
          }
          break;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcApiUtil.java (L276-278)
```java
        case FreezeBalanceContract:
          amount = contractParameter.unpack(FreezeBalanceContract.class).getFrozenBalance();
          break;
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/types/TransactionResult.java (L106-118)
```java
    if (!tx.getRawData().getContractList().isEmpty()) {
      Contract contract = tx.getRawData().getContract(0);
      byte[] fromByte = capsule.getOwnerAddress();
      byte[] toByte = getToAddress(tx);

      if (blockCapsule.getNum() == 0) {
        from = ByteArray.toJsonHex(new byte[20]);
      } else {
        from = ByteArray.toJsonHexAddress(fromByte);
      }

      to = ByteArray.toJsonHexAddress(toByte);
      value = ByteArray.toJsonHex(getTransactionAmount(contract, hash, wallet));
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L72-102)
```java
    long newBalance = accountCapsule.getBalance() - freezeBalanceContract.getFrozenBalance();

    long frozenBalance = freezeBalanceContract.getFrozenBalance();
    long expireTime = now + duration;
    byte[] ownerAddress = freezeBalanceContract.getOwnerAddress().toByteArray();
    byte[] receiverAddress = freezeBalanceContract.getReceiverAddress().toByteArray();

    long increment;
    switch (freezeBalanceContract.getResource()) {
      case BANDWIDTH:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, true,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForBandwidth(frozenBalance);
        } else {
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
        addTotalWeight(BANDWIDTH, dynamicStore, frozenBalance, increment);
        break;
      case ENERGY:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, false,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(frozenBalance);
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L126-127)
```java
    accountCapsule.setBalance(newBalance);
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L242-260)
```java
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
```
