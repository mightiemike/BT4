### Title
SELFDESTRUCT + CREATE2 redeploy inherits stale dynamic-energy `ContractState` (energyFactor/energyUsage) because `RepositoryImpl.deleteContract` never clears `ContractStateStore` - ([File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Finding Description
`RepositoryImpl.deleteContract(byte[] address)` only removes entries from `codeStore`, `accountStore`, and `contractStore`: [1](#0-0) 
It never deletes or resets the corresponding entry in `contractStateStore` (or `contractStateCache`).

`ContractStateStore.get(key)` is a plain keyed lookup with no lifecycle/versioning concept: [2](#0-1) 

`RepositoryImpl.getContractState(byte[] address)` reads straight from that store keyed purely by address, with no check against `newContractCache`/`isNewContract`: [3](#0-2) 

The dynamic-energy VM entrypoint, `Program.updateContextContractFactor()`, is invoked once per execution in `VM.play` and fetches the `ContractStateCapsule` for `getContextAddress()`. If a capsule already exists for that address it reuses it (only "catching up" the cycle via `catchUpToCycle`, which decays/increases the factor but does not reset it to zero unless `lastCycle == 0` or `lastCycle > newCycle`): [4](#0-3) [5](#0-4) 

Exploit flow:
1. Attacker deploys contract A via `CREATE2` at address X, drives up `energyUsage`/`energyFactor` in `ContractStateStore` (e.g. by exceeding `dynamicEnergyThreshold` repeatedly), or alternatively benefits from a *favorable* (low) factor left over from prior activity.
2. Attacker calls `SELFDESTRUCT`/`suicide` on contract A. `Program.suicide()` only adds the address to `getResult().addDeleteAccount(...)`, which eventually triggers `RepositoryImpl.deleteContract(address)` — this clears `contractStore`/`accountStore`/`codeStore` but leaves `contractStateStore[X]` untouched.
3. In a later transaction (or later in the same block after the destroy is committed), attacker redeploys a new contract B via `CREATE2` to the same address X (deterministic addressing via salt+init-code hash is the entire point of `CREATE2`).
4. The next call into B triggers `updateContextContractFactor()`, which looks up `ContractStateStore.get(X)` — finds the **old** capsule from contract A (non-null), and reuses its `energyFactor`/`energyUsage`/`updateCycle` instead of starting fresh, since `getContractState` has no notion that the account at X was destroyed and recreated.

This breaks the "one-time settlement per contract lifecycle" invariant: the dynamic energy accounting for a freshly-created contract deployed at a reused CREATE2 address diverges from a contract deployed at a virgin address, purely as a function of prior tenant's activity at that address, which the new contract owner did not cause and cannot control ahead of time (or can control adversarially).

### Impact Explanation
This is a state-integrity/divergence bug in TVM dynamic energy pricing: a newly deployed contract at a CREATE2-reused address can be charged more (denial-of-service / griefing on its own deployer) or benefit from an artificially suppressed penalty factor (underpriced computation) depending on the residual state left by the prior occupant, contrary to intended per-contract accounting semantics. It does not directly enable fund theft, but it is a state/accounting invariant violation that a contract deployer could exploit to manipulate the dynamic-energy penalty mechanism for contracts at a chosen CREATE2 address (e.g., pre-seed a high-usage state to make a future legitimate redeploy artificially expensive, or vice versa).

### Likelihood Explanation
Fully reachable by an unprivileged actor: deploying via `CREATE2`, calling `SELFDESTRUCT` (subject only to `VMConfig.allowTvmSelfdestructRestriction()`/related opcode availability, both of which are ordinary chain parameters, not privileged access), and redeploying at the same deterministic address is standard, permissionless TVM usage. No admin/governance action is required. The `dynamicEnergyThreshold`/`increaseFactor` mechanism must be enabled (`VMConfig.allowDynamicEnergy()`), which is a live-network-configurable proposal but not an attacker-controlled precondition beyond ordinary usage of an enabled feature.

### Recommendation
In `RepositoryImpl.deleteContract(byte[] address)`, also delete/reset the corresponding `ContractStateStore` entry (and the in-memory `contractStateCache`), e.g. call `contractStateStore.delete(address)`/mark it dirty-deleted, so a subsequent `getContractState` for a redeployed contract at the same address starts from a fresh/zeroed `ContractStateCapsule`.

### Proof of Concept
Integration test plan (JUnit, using existing `Create2Test`/`ContractStateCapsuleTest` infrastructure style):
1. Deploy contract A at address X via `CREATE2`.
2. Drive dynamic energy usage above `dynamicEnergyThreshold` across several blocks/cycles so `ContractStateStore.get(X)` has non-zero `energyFactor`.
3. Call `SELFDESTRUCT` on A, commit the transaction (through `VMActuator.execute` / `RepositoryImpl.deleteContract`).
4. Deploy contract B via `CREATE2` with the same salt/init-code hash so it lands at the same address X.
5. Trigger B and record the energy fee charged (via `program.updateContextContractFactor()`/`contextContractFactor`).
6. Deploy an identical contract C at a fresh/virgin address Y (different salt) and trigger it the same way.
7. Assert: `getContractState(X).getEnergyFactor()` after step 4 equals `0` (fresh) and the energy fee charged to B in step 5 equals the energy fee charged to C — currently this assertion fails because `ContractStateStore.get(X)` still returns contract A's stale capsule.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L486-491)
```java
  @Override
  public void deleteContract(byte[] address) {
    getCodeStore().delete(address);
    getAccountStore().delete(address);
    getContractStore().delete(address);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L520-538)
```java
  @Override
  public ContractStateCapsule getContractState(byte[] address) {
    Key key = Key.create(address);
    if (contractStateCache.containsKey(key)) {
      return new ContractStateCapsule(contractStateCache.get(key).getValue());
    }

    ContractStateCapsule contractStateCapsule;
    if (parent != null) {
      contractStateCapsule = parent.getContractState(address);
    } else {
      contractStateCapsule = getContractStateStore().get(address);
    }

    if (contractStateCapsule != null) {
      contractStateCache.put(key, Value.create(contractStateCapsule));
    }
    return contractStateCapsule;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/ContractStateStore.java (L21-24)
```java
  @Override
  public ContractStateCapsule get(byte[] key) {
    return getUnchecked(key);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2360-2383)
```java
  public long updateContextContractFactor() {
    ContractStateCapsule contractStateCapsule =
        contractState.getContractState(getContextAddress());

    if (contractStateCapsule == null) {
      contractStateCapsule = new ContractStateCapsule(
          contractState.getDynamicPropertiesStore().getCurrentCycleNumber());
      contractState.updateContractState(getContextAddress(), contractStateCapsule);
    } else {
      if (contractStateCapsule.catchUpToCycle(
          contractState.getDynamicPropertiesStore().getCurrentCycleNumber(),
          VMConfig.getDynamicEnergyThreshold(),
          VMConfig.getDynamicEnergyIncreaseFactor(),
          VMConfig.getDynamicEnergyMaxFactor(),
          VMConfig.allowStrictMath(),
          VMConfig.disableJavaLangMath())) {
        contractState.updateContractState(getContextAddress(), contractStateCapsule
        );
      }
    }
    contextContractFactor = contractStateCapsule.getEnergyFactor()
        + Constant.DYNAMIC_ENERGY_FACTOR_DECIMAL;
    return contextContractFactor;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L89-120)
```java
  public boolean catchUpToCycle(
      long newCycle, long threshold, long increaseFactor, long maxFactor,
      boolean useStrictMath, boolean disableMath
  ) {
    long lastCycle = getUpdateCycle();

    // Updated within this cycle
    if (lastCycle == newCycle) {
      return false;
    }

    // Guard judge and uninitialized state
    if (lastCycle > newCycle || lastCycle == 0L) {
      reset(newCycle);
      return true;
    }

    final long precisionFactor = DYNAMIC_ENERGY_FACTOR_DECIMAL;

    // Increase the last cycle
    // fix the threshold = 0 caused incompatible
    if (getEnergyUsage() > threshold) {
      lastCycle += 1;
      double increasePercent = 1 + (double) increaseFactor / precisionFactor;
      this.contractState = ContractState.newBuilder()
          .setUpdateCycle(lastCycle)
          .setEnergyFactor(min(
              maxFactor,
              (long) ((getEnergyFactor() + precisionFactor) * increasePercent) - precisionFactor,
              disableMath))
          .build();
    }
```
