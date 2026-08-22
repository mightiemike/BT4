### Title
Missing length validation on `PedersenHash` content before native JNI call in `IncrementalMerkleTreeContainer.append` → `PedersenHashCapsule.combine` — ([File: chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java])

### Summary
`IncrementalMerkleTreeContainer.append` stores an attacker-supplied `PedersenHash` (from a `ShieldedTransferContract.ReceiveDescription.note_commitment`, a plain protobuf `bytes` field with no fixed-size constraint enforced in Java) directly into the tree, and — once both `left` and `right` slots are filled — triggers `PedersenHashCapsule.combine()`, which forwards the raw byte arrays straight into the native `JLibrustzcash.librustzcashMerkleHash` JNI call without checking their length. Native Rust merkle-hash code expects fixed 32-byte inputs; a shorter or longer buffer passed via JNI can cause out-of-bounds native memory access.

### Finding Description
`append()` does not check the size of `obj.getContent()` before calling `treeCapsule.setLeft(obj)` / `setRight(obj)`: [1](#0-0) 

When a third element is appended (left and right already present), the code calls `PedersenHashCapsule.combine(treeCapsule.getLeft(), treeCapsule.getRight(), 0)`, which passes the raw byte content directly to the native library: [2](#0-1) 

There is no length check (`== 32`) anywhere in this call chain — `PedersenHash` is a protobuf `bytes` field that an attacker fully controls via `ReceiveDescription.note_commitment` in a `ShieldedTransferContract`, and this value flows unchanged into the tree: [3](#0-2) [4](#0-3) 

The `wfcheck()` and `isPresent()` helper methods only check for *emptiness* of content (`!getContent().isEmpty()`), not for exact 32-byte size: [5](#0-4) 

I was not able to fully confirm within this investigation whether `ShieldedTransferActuator.checkProof()` (the zk-proof/binding-signature verification step called during `validate()`, before `executeShielded()`/`append()` runs) independently rejects a `note_commitment` of the wrong byte length as a side effect of its own native calls. If it does not enforce exact 32-byte length on `note_commitment` specifically (as opposed to other proof fields like `zkproof`, `rk`, `value_commitment`), then a malformed-length commitment would reach `append()`/`combine()` unchecked.

### Impact Explanation
If the length is not otherwise enforced before reaching the native call, a JNI call from `librustzcashMerkleHash` reading a fixed 32-byte buffer from a Java byte array shorter than 32 bytes results in an out-of-bounds native memory read (or, depending on native implementation, a native-side panic/abort). This can crash the node process (denial of service) for anyone processing/re-executing the malicious transaction, matching the "Node crash" bounty impact class. Full RCE would require an additional native memory-corruption primitive beyond an over-read, which is not confirmed here.

### Likelihood Explanation
The precondition for this to be exploitable is that `checkProof()`/proof validation does not already reject an incorrectly sized `note_commitment` before `executeShielded()` is reached. Given the presence of the shielded-transaction path is gated by `dynamicStore.supportShieldedTransaction()` (a committee-controlled fork/feature flag) but is otherwise reachable by any funded account once enabled, exploitability hinges entirely on whether an unverified code path in `checkProof` allows a malformed-length `note_commitment` through. This could not be fully confirmed with the available context.

### Recommendation
Add an explicit length check (`content.size() == 32`) in `PedersenHashCapsule` (e.g., in `isPresent()`/a dedicated validator) and/or at the point where `ReceiveDescription.note_commitment` is first parsed in `ShieldedTransferActuator.validate()`, rejecting the transaction with a `ContractValidateException` before any data reaches `IncrementalMerkleTreeContainer.append` or `PedersenHashCapsule.combine`/JNI calls.

### Proof of Concept
```java
@Test
public void appendRejectsMalformedLength() {
  IncrementalMerkleTreeContainer tree = new IncrementalMerkleTreeContainer(
      new IncrementalMerkleTreeCapsule());
  PedersenHashCapsule bad = new PedersenHashCapsule();
  bad.setContent(ByteString.copyFrom(new byte[5])); // not 32 bytes
  PedersenHash a = bad.getInstance();
  PedersenHash b = bad.getInstance();
  PedersenHash c = bad.getInstance();
  tree.append(a);
  tree.append(b);
  // Expected: should throw ZksnarkException/ContractValidateException before native call
  // Actual: proceeds to PedersenHashCapsule.combine(...) -> JLibrustzcash.librustzcashMerkleHash
  // with a 5-byte array, with no prior Java-side length validation.
  tree.append(c);
}
```
Note: this JUnit demonstrates the missing Java-side guard; whether it actually crashes the JVM depends on the native library's bounds handling, which could not be verified from the Java source alone.

### Citations

**File:** chainbase/src/main/java/org/tron/common/zksnark/IncrementalMerkleTreeContainer.java (L92-103)
```java
  public void append(PedersenHash obj) throws ZksnarkException {
    if (isComplete(DEPTH)) {
      throw new ZksnarkException("tree is full");
    }
    if (!leftIsPresent()) {
      treeCapsule.setLeft(obj);
    } else if (!rightIsPresent()) {
      treeCapsule.setRight(obj);
    } else {
      PedersenHashCapsule combined =
          PedersenHashCapsule.combine(treeCapsule.getLeft(), treeCapsule.getRight(), 0);
      treeCapsule.setLeft(obj);
```

**File:** chainbase/src/main/java/org/tron/core/capsule/PedersenHashCapsule.java (L34-45)
```java
  public static PedersenHashCapsule combine(final PedersenHash a, final PedersenHash b, int depth)
      throws ZksnarkException {
    byte[] res = new byte[32];

    JLibrustzcash.librustzcashMerkleHash(new MerkleHashParams(depth, a.getContent().toByteArray(),
        b.getContent().toByteArray(), res));

    PedersenHashCapsule pedersenHashCapsule = new PedersenHashCapsule();
    pedersenHashCapsule.setContent(ByteString.copyFrom(res));

    return pedersenHashCapsule;
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/PedersenHashCapsule.java (L96-98)
```java
  public boolean isPresent() {
    return !pedersenHash.getContent().isEmpty();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L182-192)
```java
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
```

**File:** chainbase/src/main/java/org/tron/common/zksnark/MerkleContainer.java (L83-89)
```java
  public IncrementalMerkleTreeContainer saveCmIntoMerkleTree(
      IncrementalMerkleTreeContainer tree, byte[] cm) throws ZksnarkException {
    PedersenHashCapsule pedersenHashCapsule = new PedersenHashCapsule();
    pedersenHashCapsule.setContent(ByteString.copyFrom(cm));
    tree.append(pedersenHashCapsule.getInstance());
    return tree;
  }
```
