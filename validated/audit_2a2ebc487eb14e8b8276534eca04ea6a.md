### Title
ZKProofStore bypasses the revoking/rollback mechanism, causing permanent false-negative proof caching that halts legitimate shielded transactions after a chain reorg - (`actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java`)

### Summary
`ShieldedTransferActuator.checkProof` caches proof verification results (including failures) in `ZKProofStore`, keyed only by the raw transaction id, and consults this cache on every subsequent call to `validate()` before re-running the zk-SNARK checks. `ZKProofStore` extends `TronDatabase` (not `TronStoreWithRevoking`), so its writes go straight to the underlying LevelDB/RocksDB `dbSource` and are never registered with `SnapshotManager`/`RevokingDatabase`, meaning they are not part of any block-scoped session and are never rolled back on `revoke()`/`pop()`.

### Finding Description
In `checkProof`, the very first thing done is a cache lookup: [1](#0-0) 

If a prior validation attempt recorded a failure (`recordProof(tid, false)`), every future call with the same transaction id gets `ZkProofValidateException("record is fail, skip proof", false)` — with `isFirstValidated=false`, so `validate()` does not re-record it, but the false entry in `ZKProofStore` persists indefinitely: [2](#0-1) [3](#0-2) 

`ZKProofStore` is a plain `TronDatabase` subclass that writes directly to `dbSource`, with no `revokingDB`/`IRevokingDB` wiring: [4](#0-3) 

Contrast this with `TronStoreWithRevoking`, which routes every `put`/`delete` through `revokingDB` and registers itself with `RevokingDatabase` so it participates in session commit/merge/revoke during `applyBlock`/`switchFork`: [5](#0-4) 

During normal block processing and especially during `switchFork` (chain reorg), each block is applied inside an `ISession` (`revokingStore.buildSession()`), and on failure or fork-switch, `Manager` calls `revoke()`/rewinds state via the revoking store: [6](#0-5) [7](#0-6) 

Because `ZKProofStore` is not part of this revoking machinery, a proof-cache entry written while processing a transaction on a since-abandoned fork (or during a transient node-local condition, e.g., a temporary/incorrect `totalShieldedPoolValue` on a losing branch, or a transient issue in the native `librustzcash` call causing a spurious `ZksnarkException`) is never undone. If a shielded transaction fails `checkProof` on the losing/rolled-back branch (recording `false` for its transaction id), and the exact same transaction (same tx id — the hash is deterministic over its full content) is later legitimately re-broadcast or replayed on the canonical chain, `checkProof` will short-circuit to the cached `false` and reject it every time thereafter, even though the transaction is valid on the current canonical state. This is a state-divergence/liveness bug: the node's local `ZKProofStore` diverges from the canonical, revoked chain state that all other stores correctly reflect.

### Impact Explanation
A legitimate shielded transaction can be permanently and irrevocably blocked from ever being accepted by an affected node once its transaction id has been recorded as `false` in `ZKProofStore`, regardless of any subsequent state changes (chain reorgs, pool value changes) that would otherwise make it valid. This is a denial-of-service against a specific transaction/user on any node that processed the losing fork, and — because `ZKProofStore` state is not consensus-checkpointed/rolled back like other stores — it represents a divergence between the node's transient/reorg-related in-memory assumptions and its persisted zk-proof cache, undermining the correctness guarantee that all other `chainbase` stores rely on (rollback consistency across `checkProof`'s own session boundaries).

### Likelihood Explanation
This requires the tx to first be included/validated on a chain branch that is later abandoned via `switchFork`/`pop`, and for `checkProof` to record a `false` result before the fork switch (e.g., due to `totalShieldedPoolValue` differing between branches, or a transient environment-dependent native-library failure). Chain reorgs on DPoS are a normal, expected occurrence (not attacker-privileged), and a user resubmitting the exact same signed shielded transaction after a reorg is a realistic and unprivileged scenario. The bug does not require any special privilege — it is triggered purely by ordinary chain-reorg dynamics plus resubmission of the same transaction.

### Recommendation
Make `ZKProofStore` participate in the revoking mechanism by having it extend/delegate to the same `revokingDB`/`IRevokingDB` infrastructure used by `TronStoreWithRevoking` (or by manually wrapping `ZKProofStore.put` calls within the same `ISession` used for block application), so that proof-cache entries are rolled back consistently with all other chain state on `revoke()`/`pop()`/fork-switch. Alternatively, scope/key the cache to include the state root or block context so stale entries from abandoned forks cannot leak into subsequent validation on a different canonical state, or avoid caching hard failures at all and only cache successful proof verifications (removing the `false`-result fast path entirely) since failed transactions are cheap to re-verify and caching failures provides limited DoS-protection benefit relative to this correctness risk.

### Proof of Concept
Java integration test plan (using `BaseTest`/`Manager` test harness already present in the repo, e.g. patterned after `ShieldedReceiveTest`/`ManagerMockTest`):

1. Build a valid shielded transaction `T` (spend + receive descriptions) via `ZenTransactionBuilder`, matching the pattern in `framework/src/test/java/org/tron/core/zksnark/ShieldedReceiveTest.java`.
2. Apply block `B1` containing `T` on branch A such that `checkProof` fails for a state-dependent reason (e.g., set `totalShieldedPoolValue` on branch A so that `totalShieldedPoolValue < 0` check trips, or a wraps `ArithmeticException`), causing `recordProof(tid, false)` to persist in `ZKProofStore`.
3. Trigger `switchFork` to a competing branch B where `B1` (and `T`) is never applied (use `Manager.switchFork` via reflection as in `ManagerMockTest.testSwitchForkRejectsBlockWithInvalidSignature`), and verify via `dbManager.getChainBaseManager().getAccountStore()`/`getDynamicPropertiesStore()` that account/pool state has been correctly rolled back to pre-`B1` values.
4. Re-submit the identical transaction `T` (same tx id) for validation on branch B's canonical state, where `totalShieldedPoolValue` is now sufficient and the transaction should validate successfully.
5. Assert that `ShieldedTransferActuator.validate()` still throws `ZkProofValidateException("record is fail, skip proof", false)` because `proofStore.has(tid)` returns `true` with a cached `false`, even though all other state (`AccountStore`, `DynamicPropertiesStore.getTotalShieldedPoolValue()`) has been correctly rolled back — proving `ZKProofStore` diverged from consensus-rolled-back state.
6. Additionally assert (via `ZKProofStoreTest`-style direct access) that `proofStore.get(tid)` still returns `false` after the equivalent of a `revokingStore.revoke()`/`pop()` cycle that successfully reverted `AccountStore`/`NullifierStore` entries written in the same block, confirming `ZKProofStore` is not registered with `RevokingDatabase`/`SnapshotManager`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L262-270)
```java
    //check spendProofs receiveProofs and Binding sign hash
    try {
      checkProof(spendDescriptions, receiveDescriptions, fee);
    } catch (ZkProofValidateException e) {
      if (e.isFirstValidated()) {
        recordProof(tx.getTransactionId(), false);
      }
      throw e;
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L277-285)
```java
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    if (proofStore.has(tx.getTransactionId().getBytes())) {
      if (proofStore.get(tx.getTransactionId().getBytes())) {
        return;
      } else {
        throw new ZkProofValidateException("record is fail, skip proof", false);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L360-363)
```java
  private void recordProof(Sha256Hash tid, boolean result) {
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    proofStore.put(tid.getBytes(), result);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/ZKProofStore.java (L1-20)
```java
package org.tron.core.store;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationContext;
import org.springframework.stereotype.Component;
import org.tron.core.db.TronDatabase;

@Component
public class ZKProofStore extends TronDatabase<Boolean> {

  @Autowired
  public ZKProofStore(ApplicationContext ctx) {
    super("zkProof");
  }

  @Override
  public void put(byte[] key, Boolean item) {
    byte[] b = {(byte) (item.booleanValue() ? 0x01 : 0x00)};
    dbSource.putData(key, b);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java (L82-100)
```java
  @PostConstruct
  private void init() {
    revokingDatabase.add(revokingDB);
    dbStatService.register(db);
  }

  @Override
  public void put(byte[] key, T item) {
    if (Objects.isNull(key) || Objects.isNull(item)) {
      return;
    }

    revokingDB.put(key, item.getData());
  }

  @Override
  public void delete(byte[] key) {
    revokingDB.delete(key);
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1143-1157)
```java
        try (ISession tmpSession = revokingStore.buildSession()) {
          if (!item.getBlk().validateSignature(
              getDynamicPropertiesStore(), getAccountStore())) {
            throw new ValidateSignatureException(
                "switch fork: block " + item.getBlk().getNum() + " signature invalid");
          }
          // The new branch is applied on a rewound, diverged state where account permissions
          // may have changed, so a cached signature-verification result is no longer
          // trustworthy. Clear it to force every transaction to re-validate its signature
          // against the fork-chain state.
          for (TransactionCapsule tx : item.getBlk().getTransactions()) {
            tx.setVerified(false);
          }
          applyBlock(item.getBlk().setSwitch(true));
          tmpSession.commit();
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1389-1397)
```java
            try (ISession tmpSession = revokingStore.buildSession()) {
              applyBlock(newBlock, txs);
              tmpSession.commit();
            } catch (Throwable throwable) {
              logger.error(throwable.getMessage(), throwable);
              khaosDb.removeBlk(block.getBlockId());
              clearSolidityContractTriggerCache(block.getNum());
              throw throwable;
            }
```
