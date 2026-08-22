### Title
Permanent DoS of TRON shielded transactions via single global Merkle tree exhaustion in `ShieldedTransferActuator` / `MerkleContainer` - (File: `chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java`)

### Summary
`java-tron`'s privacy feature (shielded/zk-SNARK transfers) maintains exactly one global note-commitment Merkle tree (`CURRENT_TREE`) for the entire chain, with a fixed depth of `32`, i.e. a hard cap of `2^32` leaves. Any `ShieldedTransferContract` broadcast by any unprivileged account appends its `ReceiveDescription` commitments to this single tree. Once the tree reaches its maximum capacity, the append operation throws an unrecoverable exception and every subsequent shielded transfer/mint that includes a receive description will permanently fail — this is the same bug class as the reported Morph `L2ToL1MessagePasser` merkle-tree-exhaustion DoS: a single, globally shared, capacity-bounded tree with no rotation/reset mechanism, that any user can push toward exhaustion.

### Finding Description
`MerkleContainer.getCurrentMerkle()`/`setCurrentMerkle()` persist a single tree under the fixed key `CURRENT_TREE` in `incrementalMerkleTreeStore`: [1](#0-0) 

`ShieldedTransferActuator.executeShielded` (invoked from every broadcast `ShieldedTransferContract` transaction, an unprivileged, anonymous-reachable RPC/broadcast path) fetches this single global tree and appends every `ReceiveDescription` commitment to it via `MerkleContainer.saveCmIntoMerkleTree`: [2](#0-1) 

The append logic lives in `IncrementalMerkleTreeContainer.append`, which is bounded by a fixed `DEPTH = 32`, meaning the tree can hold at most `2^32` leaves before `isComplete(DEPTH)` becomes true and any further append throws `"tree is full"`: [3](#0-2) 

This mirrors the Morph `Tree.sol::_appendMessageHash` root cause exactly: one shared tree, hard leaf cap, and no mitigation path once full — any account calling `L2CrossDomainMessenger.sendMessage` (analog: any account submitting a `ShieldedTransferContract` with a receive description) permanently fails once the shared structure saturates.

Since `CURRENT_TREE`/`LAST_TREE` are single well-known keys with no per-epoch or per-shard rotation, once the tree is exhausted there is no code path to reset it or start a fresh tree — every future shielded mint/transfer with a `ReceiveDescription` will hit the "tree is full" exception in `executeShielded` and be rejected as `FAILED`, permanently.

### Impact Explanation
Once the global commitment tree is exhausted, no new value can ever be shielded (minted) or transferred within the shielded pool again — this is a permanent Denial-of-Service of the entire shielded-transaction subsystem, matching the class of "asset/accounting corruption / DoS via protocol implementation" called out as in-scope. Existing shielded balances could potentially still be spent (`spend` only requires an existing anchor/nullifier, not a tree append) but any new deposit into the privacy pool is permanently blocked, degrading protocol functionality without any way to recover short of a hard fork/patch.

### Likelihood Explanation
Reaching `2^32` leaves requires appending roughly 4.29 billion note commitments. Unlike the Morph case (a cheap `sendMessage` call), each `ReceiveDescription` requires generating a valid zk-SNARK proof, which is computationally far more expensive. However, the attack is still feasible:
- An attacker can batch multiple `ReceiveDescription`s in a single `ShieldedTransferContract` transaction (each one appends one leaf).
- The cost is dominated by proof generation, not on-chain execution.
- Over months or years of normal protocol operation, the tree will eventually fill naturally if the shielded pool sees sustained adoption.
- Once full, the failure is permanent and unrecoverable without protocol intervention.

### Recommendation
Implement a tree rotation/reset mechanism: either (1) create a new tree per block/epoch and store historical trees by block number (similar to how `merkleTreeIndexStore` already indexes trees by block), (2) use a rolling window of multiple concurrent trees, or (3) implement a "tree full" handler that gracefully transitions to a new tree rather than reverting all transactions. The current design assumes the tree will never fill, which is a false assumption.

### Proof of Concept
An attacker can construct a `ShieldedTransferContract` with multiple `ReceiveDescription` entries (each with a valid zk-SNARK proof) and broadcast it repeatedly. Each transaction appends `N` leaves to the global tree. After approximately `2^32 / N` transactions, the tree becomes full. The next transaction will fail in `ShieldedTransferActuator.executeShielded` at line 186 when `merkleContainer.saveCmIntoMerkleTree` calls `tree.append()`, which throws `ZksnarkException("tree is full")`, causing the transaction to be rejected with status `FAILED` and the exception to propagate up, permanently blocking all future shielded transactions with receive descriptions.

Example pseudo-code:
```java
// Attacker broadcasts repeatedly:
ShieldedTransferContract tx = new ShieldedTransferContract();
for (int i = 0; i < 100; i++) {
  ReceiveDescription rd = generateValidReceiveDescription(); // includes zk-SNARK proof
  tx.addReceiveDescription(rd);
}
// After ~2^32 / 100 such transactions, the global CURRENT_TREE is exhausted.
// All subsequent shielded transactions fail permanently.
``` [3](#0-2)

### Citations

**File:** chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java (L36-46)
```java
  public IncrementalMerkleTreeContainer getCurrentMerkle() {
    IncrementalMerkleTreeCapsule capsule = incrementalMerkleTreeStore.get(currentTreeKey);
    if (capsule == null) {
      return getBestMerkle();
    }
    return capsule.toMerkleTreeContainer();
  }

  public void setCurrentMerkle(IncrementalMerkleTreeContainer treeContainer) {
    incrementalMerkleTreeStore.put(currentTreeKey, treeContainer.getTreeCapsule());
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L174-193)
```java
    IncrementalMerkleTreeContainer currentMerkle = merkleContainer.getCurrentMerkle();
    try {
      currentMerkle.wfcheck();
    } catch (ZksnarkException e) {
      ret.setStatus(fee, code.FAILED);
      ret.setShieldedTransactionFee(fee);
      throw new ContractExeException(e.getMessage());
    }
    //handle receives
    for (ReceiveDescription receive : receives) {
      try {
        merkleContainer
            .saveCmIntoMerkleTree(currentMerkle, receive.getNoteCommitment().toByteArray());
      } catch (ZksnarkException e) {
        ret.setStatus(0, code.FAILED);
        ret.setShieldedTransactionFee(fee);
        throw new ContractExeException(e.getMessage());
      }
    }
    merkleContainer.setCurrentMerkle(currentMerkle);
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java (L92-95)
```java
  public void append(PedersenHash obj) throws ZksnarkException {
    if (isComplete(DEPTH)) {
      throw new ZksnarkException("tree is full");
    }
```
