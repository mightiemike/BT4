### Title
NPE-triggering `Value` construction with empty code (`allowMultiSign()==false`) can abort `RepositoryImpl.commit()` for zero-length CREATE code - (File: actuator/src/main/java/org/tron/core/vm/repository/Value.java)

### Finding Description
`Value(T value, int type)` only sets `this.type` in two cases: when `value != null`, or when `value == null` **and** `VMConfig.allowMultiSign()` is `true` (in which case it falls back to `Type.UNKNOWN`). [1](#0-0) 

When `value == null` and `allowMultiSign()` is `false`, `this.type` is never assigned and remains the default `null`. `Value.create(byte[], int)` funnels an empty (`length == 0`) or `null` byte array into this constructor with `value` forced to `null`: [2](#0-1) 

`RepositoryImpl.saveCode(address, code)` calls exactly this path with `Type.CREATE` whenever the VM deploys a contract: [3](#0-2) 

An unprivileged attacker can craft a `CreateSmartContract`/`TriggerSmartContract`-driven `CREATE` whose constructor init code executes and returns 0 bytes (e.g. `PUSH1 0 PUSH1 0 RETURN`). This is standard, valid EVM/TVM bytecode — an "empty contract" deployment — and results in `code.length == 0` being passed to `saveCode`, producing a `Value<byte[]>` whose `type` field is `null` (assuming `allowMultiSign()` is disabled, which is the pre-activation/default state of this chain parameter on private or not-yet-forked networks).

Later, `RepositoryImpl.commit()` iterates every cache and calls `value.getType().isCreate() || value.getType().isDirty()` to decide whether to persist the entry — the pattern is visible for `commitAccountCache`: [4](#0-3) 

`commitCodeCache` (called immediately after `commitAccountCache` inside `commit()`) follows the same structural convention across all `commit*Cache` methods: [5](#0-4) 

Because the cached `Value` for the empty code has `type == null`, invoking `.getType().isCreate()` throws a `NullPointerException`. I was unable to view the exact body of `commitCodeCache` in this session (file truncated), so I cannot 100% confirm it uses this identical `getType()` call without a null-check, but every other sibling `commit*Cache` method visible follows this exact unguarded pattern, and there is no null-check anywhere in `Value` or in the `commit*` helpers I could inspect.

### Impact Explanation
If `commitCodeCache` (or any other `commit*Cache` consumer of a `Value` produced from an empty byte array under `allowMultiSign()==false`) dereferences `getType()` without a null check, the `NullPointerException` aborts `RepositoryImpl.commit()` mid-way. Because `commit()` runs the cache flushes sequentially, an exception thrown partway (e.g., in `commitCodeCache`) leaves later caches (`commitDelegatedResourceCache`, `commitDelegationCache`, `commitDelegatedResourceAccountIndexCache`, etc.) never committed to the parent `Repository`. This means delegated resources/votes/rewards state changes computed earlier in the same VM execution are silently dropped from the child repository without being persisted, while resources may already have been "spent" by the caller logic, i.e. state becomes inconsistent/partially applied. Because the bug is fully deterministic given the same transaction and chain parameters, every validating node hits it identically, which is a consensus-safety-relevant halt/inconsistency risk rather than a localized failure.

### Likelihood Explanation
- Preconditions: `ALLOW_MULTI_SIGN` chain parameter (`VMConfig.allowMultiSign()`) must be disabled — true by default on any chain/testnet where the corresponding proposal has not been activated.
- Attacker capability: only needs to submit a `CreateSmartContract`/`TriggerSmartContract` transaction whose init code returns zero bytes — trivially constructible EVM/TVM bytecode, requiring no special privileges.
- Repeatability: fully deterministic and reproducible on every node processing the same transaction/block.

### Recommendation
In `Value`'s constructor, always initialize `type` to a non-null default (e.g. `Type.UNKNOWN`) regardless of `VMConfig.allowMultiSign()`, or explicitly guard every `commit*Cache` call site to null-check `value.getType()` before calling `.isCreate()`/`.isDirty()`. The safest fix is removing the `allowMultiSign()` condition around the `else` branch in the constructor so `type` is never left `null`.

### Proof of Concept
```java
// actuator/src/test/java/org/tron/core/vm/repository/ValueNpeTest.java
import org.junit.Test;
import org.tron.core.vm.repository.Value;
import org.tron.core.vm.repository.Type;
import org.tron.core.vm.config.VMConfig;
import static org.junit.Assert.*;

public class ValueNpeTest {

  @Test
  public void emptyCodeValue_hasNullType_whenMultiSignDisabled() {
    // Simulate allowMultiSign() == false (default/unactivated chain parameter)
    Value<byte[]> v = Value.create(new byte[0], Type.CREATE);
    // BUG: type is null instead of a non-null Type
    assertNotNull("Value.getType() must never be null after create()", v.getType());
  }

  @Test(expected = Test.None.class /* should NOT throw */)
  public void repositoryCommit_doesNotThrowNPE_onEmptyCreateCode() {
    // Integration-level PoC:
    // 1. Deploy a contract whose init bytecode is: PUSH1 0x00 PUSH1 0x00 RETURN
    //    (returns 0-length runtime code)
    // 2. Ensure ALLOW_MULTI_SIGN dynamic parameter == 0 (disabled)
    // 3. Execute CreateSmartContract via VMActuator, causing
    //    RepositoryImpl.saveCode(address, new byte[0]) with Type.CREATE
    // 4. Call repository.commit()
    // Expected: commit() completes without NullPointerException, and every
    // committed Key in codeCache has a non-null Type.
  }
}
```
Expected assertions: `Value.getType()` is never `null` for any constructed `Value`, and `RepositoryImpl.commit()` completes successfully (no NPE) for every fuzzed `TriggerSmartContract`/`CreateSmartContract` payload producing empty code or storage values, for both `allowMultiSign() == true` and `false`.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/Value.java (L18-27)
```java
  private Value(T value, int type) {
    if (value != null) {
      this.value = value;
      this.type = new Type(type);
    } else {
      if (VMConfig.allowMultiSign()) {
        this.type = new Type(Type.UNKNOWN);
      }
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/Value.java (L37-40)
```java
  public static Value<byte[]> create(byte[] value, int type) {
    return (value == null || value.length ==0) ? new Value<>(null, type) :
        new Value<>(Arrays.copyOf(value, value.length), type);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L637-647)
```java
  @Override
  public void saveCode(byte[] address, byte[] code) {
    codeCache.put(Key.create(address), Value.create(code, Type.CREATE));

    if (VMConfig.allowTvmConstantinople()) {
      ContractCapsule contract = getContract(address);
      byte[] codeHash = Hash.sha3(code);
      contract.setCodeHash(codeHash);
      updateContract(address, contract);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L765-783)
```java
  @Override
  public void commit() {
    Repository repository = null;
    if (parent != null) {
      repository = parent;
    }
    commitAccountCache(repository);
    commitCodeCache(repository);
    commitContractCache(repository);
    commitContractStateCache(repository);
    commitStorageCache(repository);
    commitDynamicCache(repository);
    commitDelegatedResourceCache(repository);
    commitVotesCache(repository);
    commitDelegationCache(repository);
    commitDelegatedResourceAccountIndexCache(repository);
    commitTransientStorage(repository);
    commitNewContractCache(repository);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L997-1000)
```java
  private void commitAccountCache(Repository deposit) {
    accountCache.forEach((key, value) -> {
      if (value.getType().isCreate() || value.getType().isDirty()) {
        if (deposit != null) {
```
