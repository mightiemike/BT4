### Title
Value.create(byte[], int) silently drops the `type` field for zero-length values, causing NPE in RepositoryImpl.commit() when allowMultiSign is inactive - (File: actuator/src/main/java/org/tron/core/vm/repository/Value.java)

### Finding Description
`Value`'s private constructor only assigns `this.type` in two cases: when `value != null` it builds `new Type(type)`, and when `value == null` **and** `VMConfig.allowMultiSign()` is `true` it sets `Type.UNKNOWN`. If `value == null` and `allowMultiSign()` is `false`, the `type` field is left at its default (`null`), and the caller-supplied `type` argument is silently discarded. [1](#0-0) 

`Value.create(byte[] value, int type)` routes any zero-length array through this null-value branch: [2](#0-1) 

`RepositoryImpl.saveCode()` is the attacker-reachable entry point: it always stores contract bytecode via `Value.create(code, Type.CREATE)` without checking for zero length. [3](#0-2) 

`saveCode` is invoked directly for every `CreateSmartContract` transaction in `VMActuator.execute()` (for non-Constantinople chains) and in `Program.createContractImpl()` for internal `CREATE`/`CREATE2` (for Constantinople chains), whenever the constructor's `RETURN` produces zero-length runtime code — a fully legal, unprivileged deployment pattern (e.g., a constructor that runs logic and returns no runtime bytecode): [4](#0-3) [5](#0-4) 

When `commit()` runs, `commitCodeCache()` unconditionally calls `value.getType().isDirty()`: [6](#0-5) 

Since `type` is `null` in this scenario, `.isDirty()` throws a `NullPointerException`. The same defect applies identically to `commitDynamicCache()` and `commitDelegationCache()`, which are fed by `updateDynamicProperty`/`updateDelegation` using `Value.create(bytesCapsule.getData(), Type.DIRTY)` — if the stored byte array is empty, the same NPE path is triggered. [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9) 

No auth, accounting, replay, or fee guard intercepts this: the deployment transaction is charged normal create/energy fees and proceeds to `saveCode` and eventual `commit()` regardless of the code being empty. The only preventing factor is the runtime value of `VMConfig.allowMultiSign()`, a chain-parameter flag unrelated to code-storage correctness, whose "off" state is a real, reachable network condition (e.g. freshly bootstrapped/private chains or networks where the corresponding proposal has not been activated).

### Impact Explanation
An unprivileged attacker who deploys a contract whose constructor returns zero-length runtime code (fully valid TVM semantics) triggers an uncaught `NullPointerException` inside `RepositoryImpl.commit()`, invoked from `VMActuator.execute()`/`Program.createContractImpl()` during real transaction/block processing. Because this executes in the consensus-critical transaction-processing path, any node with `allowMultiSign()` evaluating false that processes this transaction will throw and fail to commit the block, producing a transaction-processing/consensus-halt DoS across all nodes sharing that configuration.

### Likelihood Explanation
Trivially reproducible by any account with enough TRX for a `CreateSmartContract` transaction: deploy a contract whose init code executes `RETURN(0,0)`. No special privileges or governance actions are needed by the attacker; the only precondition is a network state where `VMConfig.allowMultiSign()` returns false (an already-existing, deterministic node/chain configuration state, not an attacker action). Given zero-length deployed code is legal and simple to craft, this is highly feasible and fully repeatable.

### Recommendation
Fix `Value`'s private constructor so `type` is always initialized to a `Type` object regardless of whether `value` is null and regardless of `allowMultiSign()`:
```java
private Value(T value, int type) {
  this.value = value;
  this.type = VMConfig.allowMultiSign() && value == null ? new Type(Type.UNKNOWN) : new Type(type);
}
```
This preserves existing UNKNOWN-marking semantics for the `allowMultiSign` case while guaranteeing `type` is never null, eliminating the NPE regardless of `allowMultiSign` state or array length.

### Proof of Concept
```java
// actuator/src/test/java/org/tron/core/vm/repository/ValueNpeTest.java
import org.junit.jupiter.api.Test;
import org.tron.core.vm.repository.Type;
import org.tron.core.vm.repository.Value;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

public class ValueNpeTest {

  @Test
  void createWithEmptyByteArray_shouldNotProduceNullType() {
    // Simulate allowMultiSign() == false (default/unmocked VMConfig state)
    Value<byte[]> value = Value.create(new byte[0], Type.CREATE);

    // Expect: getType() must never be null; isDirty()/isCreate() should be callable safely
    assertDoesNotThrow(() -> value.getType().isDirty(),
        "Value.getType() returned null for zero-length byte[] when allowMultiSign() is false");
  }
}
```
Integration-level PoC: submit a `CreateSmartContract` transaction whose init bytecode is `60006000f3` (`PUSH1 0 PUSH1 0 RETURN`, i.e., returns 0-length code), with `allowMultiSign` proposal not yet enabled on the test chain (`ProposalUtil`/`DynamicPropertiesStore` default), then call `VMActuator.execute()`; assert that a `NullPointerException` is currently thrown in `RepositoryImpl.commitCodeCache()` (reproducing the bug), and that after the fix the transaction processes normally with an account created with empty code.

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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L581-585)
```java
  @Override
  public void updateDynamicProperty(byte[] word, BytesCapsule bytesCapsule) {
    dynamicPropertiesCache.put(Key.create(word),
        Value.create(bytesCapsule.getData(), Type.DIRTY));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L619-623)
```java
  @Override
  public void updateDelegation(byte[] word, BytesCapsule bytesCapsule) {
    delegationCache.put(Key.create(word),
        Value.create(bytesCapsule.getData(), Type.DIRTY));
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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1009-1019)
```java
  private void commitCodeCache(Repository deposit) {
    codeCache.forEach(((key, value) -> {
      if (value.getType().isDirty() || value.getType().isCreate()) {
        if (deposit != null) {
          deposit.putCode(key, value);
        } else {
          getCodeStore().put(key.getData(), new CodeCapsule(value.getValue()));
        }
      }
    }));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1063-1073)
```java
  private void commitDynamicCache(Repository deposit) {
    dynamicPropertiesCache.forEach(((key, value) -> {
      if (value.getType().isDirty() || value.getType().isCreate()) {
        if (deposit != null) {
          deposit.putDynamicProperty(key, value);
        } else {
          getDynamicPropertiesStore().put(key.getData(), new BytesCapsule(value.getValue()));
        }
      }
    }));
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1099-1109)
```java
  private void commitDelegationCache(Repository deposit) {
    delegationCache.forEach((key, value) -> {
      if (value.getType().isDirty() || value.getType().isCreate()) {
        if (deposit != null) {
          deposit.putDelegation(key, value);
        } else {
          getDelegationStore().put(key.getData(), new BytesCapsule(value.getValue()));
        }
      }
    });
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L202-222)
```java
        if (TrxType.TRX_CONTRACT_CREATION_TYPE == trxType && !result.isRevert()) {
          byte[] code = program.getResult().getHReturn();
          if (code.length != 0 && VMConfig.allowTvmLondon() && code[0] == (byte) 0xEF) {
            if (null == result.getException()) {
              result.setException(Program.Exception.invalidCodeException());
            }
          }
          long saveCodeEnergy = (long) getLength(code) * EnergyCost.getCreateData();
          long afterSpend = program.getEnergyLimitLeft().longValue() - saveCodeEnergy;
          if (afterSpend < 0) {
            if (null == result.getException()) {
              result.setException(Program.Exception
                  .notEnoughSpendEnergy("save just created contract code",
                      saveCodeEnergy, program.getEnergyLimitLeft().longValue()));
            }
          } else {
            result.spendEnergy(saveCodeEnergy);
            if (VMConfig.allowTvmConstantinople()) {
              rootRepository.saveCode(program.getContractAddress().getNoLeadZeroesData(), code);
            }
          }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L924-944)
```java
    // 4. CREATE THE CONTRACT OUT OF RETURN
    byte[] code = createResult.getHReturn();

    if (code.length != 0 && VMConfig.allowTvmLondon() && code[0] == (byte) 0xEF) {
      createResult.setException(Program.Exception
          .invalidCodeException());
    }

    long saveCodeEnergy = (long) getLength(code) * EnergyCost.getCreateData();

    long afterSpend =
        programInvoke.getEnergyLimit() - createResult.getEnergyUsed() - saveCodeEnergy;
    if (!createResult.isRevert()) {
      if (afterSpend < 0) {
        createResult.setException(
            Exception.notEnoughSpendEnergy("No energy to save just created contract code",
                saveCodeEnergy, programInvoke.getEnergyLimit() - createResult.getEnergyUsed()));
      } else {
        createResult.spendEnergy(saveCodeEnergy);
        deposit.saveCode(newAddress, code);
      }
```
