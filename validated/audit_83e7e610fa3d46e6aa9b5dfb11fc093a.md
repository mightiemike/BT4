### Title
Unauthenticated zk-SNARK proof generation is always executed before anchor/nullifier validity is checked, enabling CPU-exhaustion via garbage vouchers - (File: framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java)

### Summary
`ZenTransactionBuilder.build()` calls `generateSpendProof`/`generateOutputProof` for every attacker-supplied `SpendDescriptionInfo`/`ReceiveDescriptionInfo` unconditionally, executing `voucher.path().encode()` and the native `librustzcashSaplingSpendProof`/`librustzcashSaplingOutputProof` zk-SNARK proving calls before any check that the supplied anchor/voucher corresponds to a real, existing note-commitment tree state. This lets an unauthenticated caller of `CreateShieldedTransactionWithoutSpendAuthSig` force the node to perform expensive proof computation on fully fabricated (never-committed) vouchers/anchors.

### Finding Description
`build(boolean withAsk)` iterates `spends` and `receives` and calls `generateSpendProof` / `generateOutputProof` directly [1](#0-0) . Inside `generateSpendProof`, the only checks performed before the proof call are that `cm`/`nf` are non-empty byte arrays — no check that `spend.anchor` matches any real Merkle root, and no check that `spend.voucher` was derived from an actual on-chain commitment tree [2](#0-1) . The (expensive) native zk-proof generation `JLibrustzcash.librustzcashSaplingSpendProof` is then invoked with these attacker-controlled anchor/voucher-path bytes [3](#0-2) . Likewise `generateOutputProof` only validates that `cm` is non-empty and that `ovk` is 32 bytes before running `librustzcashSaplingOutputProof` [4](#0-3) .

There is no anchor-existence check (against the actual incremental Merkle tree/anchor set maintained by chain state) anywhere in this builder before the proof calls run. Any real anchor/nullifier/Merkle-root validity check happens later, only when the resulting transaction is submitted and processed by the shielded-transfer actuator during transaction execution — which is entirely separate from, and after, this builder's proof-generation step. Thus the expensive cryptographic work (elliptic-curve-based zk-SNARK proving, non-trivial CPU cost) is fully paid for any syntactically-valid-but-garbage input, and the resulting transaction can never be accepted on-chain since the anchor doesn't exist.

### Impact Explanation
This builder is exercised by the `CreateShieldedTransactionWithoutSpendAuthSig` gRPC/HTTP API, which is intended to let external/light wallets ask a full node to construct and prove shielded transactions on their behalf. Because proof generation runs unconditionally on unvalidated anchors, a caller can submit repeated requests with random/garbage vouchers and anchors, forcing the node to perform full zk-SNARK proof generation (CPU-expensive) for spends/outputs that will never be able to settle. Repeated abuse wastes full-node CPU per request with no compensating cost to the caller — a public compute-exhaustion vector scoped to nodes exposing this API.

### Likelihood Explanation
The precondition is simply that the node exposes the `CreateShieldedTransactionWithoutSpendAuthSig` API (HTTP servlet `CreateShieldedTransactionWithoutSpendAuthSigServlet` / gRPC in `Wallet.java`), which is a standard, unauthenticated public API in java-tron's default full-node configuration. The attacker only needs to supply syntactically-valid `Note`/`IncrementalMerkleVoucherContainer` structures with fabricated data — no privileged access or valid funds required — and can repeat the call arbitrarily, making this trivially and repeatably exploitable as a resource-exhaustion vector, though its practical severity depends on node-level rate limiting/config (not addressed within this file) and does not by itself allow theft, replay, or double-settlement since such transactions can never be accepted on-chain.

### Recommendation
Before performing spend/output proof generation in `ZenTransactionBuilder.build()`/`generateSpendProof`, validate that the supplied anchor corresponds to a real, currently-tracked Merkle root (e.g., cross-check against the node's known anchor set via `MerkleContainer`/`checkMerkleRoot`-style logic) and reject requests with unknown anchors early, before invoking `voucher.path().encode()` or any native proving call. Additionally, apply request-level rate limiting/cost accounting to the `CreateShieldedTransactionWithoutSpendAuthSig` endpoint to bound the CPU cost an unauthenticated caller can impose per unit time.

### Proof of Concept
```java
// Java unit test (JUnit) in framework/src/test/java/org/tron/core/zksnark/
@Test
public void testProofGeneratedBeforeAnchorValidation() throws Exception {
  ZenTransactionBuilder builder = new ZenTransactionBuilder(wallet);
  // Construct a syntactically valid Note + fresh (never-committed) IncrementalMerkleVoucherContainer
  IncrementalMerkleVoucherContainer garbageVoucher = new IncrementalMerkleVoucherContainer(/* empty/garbage tree */);
  byte[] fakeAnchor = TransactionCapsule.getShieldTransactionHashIgnoreTypeException(...); // arbitrary 32 random bytes
  Note note = new Note(paymentAddress, 100L);

  builder.addSpend(expsk, note, fakeAnchor, garbageVoucher);

  long start = System.nanoTime();
  Assertions.assertThrows(ZksnarkException.class, () -> builder.buildWithoutAsk());
  long elapsed = System.nanoTime() - start;

  // Assert that proof-generation cost was paid (elapsed time consistent with zk-proof computation,
  // e.g. > baseline threshold), confirming compute is spent before any anchor-existence
  // check (which does not exist in this call path at all).
  Assertions.assertTrue(elapsed > EXPECTED_PROOF_COMPUTE_NS_THRESHOLD);
}
```
Fuzz plan: generate N random anchors/vouchers, call `builder.addSpend(...)` + `builder.buildWithoutAsk()` for each, and confirm `generateSpendProof` always reaches and executes `librustzcashSaplingSpendProof` (instrument via logging/mock) regardless of anchor validity, demonstrating the missing early-reject/anchor-check gate.

### Citations

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L143-153)
```java
      // Create SpendDescriptions
      for (SpendDescriptionInfo spend : spends) {
        SpendDescriptionCapsule spendDescriptionCapsule = generateSpendProof(spend, ctx);
        contractBuilder.addSpendDescription(spendDescriptionCapsule.getInstance());
      }

      // Create OutputDescriptions
      for (ReceiveDescriptionInfo receive : receives) {
        ReceiveDescriptionCapsule receiveDescriptionCapsule = generateOutputProof(receive, ctx);
        contractBuilder.addReceiveDescription(receiveDescriptionCapsule.getInstance());
      }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L214-235)
```java
    byte[] cm = spend.note.cm();

    // check if ak exists
    byte[] ak;
    byte[] nf;
    byte[] nsk;
    if (!ArrayUtils.isEmpty(spend.ak)) {
      ak = spend.ak;
      nf = spend.note.nullifier(ak, JLibrustzcash.librustzcashNskToNk(spend.nsk),
          spend.voucher.position());
      nsk = spend.nsk;
    } else {
      ak = spend.expsk.fullViewingKey().getAk();
      nf = spend.note.nullifier(spend.expsk.fullViewingKey(), spend.voucher.position());
      nsk = spend.expsk.getNsk();
    }

    if (ByteArray.isEmpty(cm) || ByteArray.isEmpty(nf)) {
      throw new ZksnarkException("Spend is invalid");
    }

    byte[] voucherPath = spend.voucher.path().encode();
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L240-254)
```java
    if (!JLibrustzcash.librustzcashSaplingSpendProof(
        new SpendProofParams(ctx,
            ak,
            nsk,
            spend.note.getD().getData(),
            spend.note.getRcm(),
            spend.alpha,
            spend.note.getValue(),
            spend.anchor,
            voucherPath,
            cv,
            rk,
            zkproof))) {
      throw new ZksnarkException("Spend proof failed");
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L266-293)
```java
      throws ZksnarkException {
    byte[] cm = output.getNote().cm();
    if (ByteArray.isEmpty(cm)) {
      throw new ZksnarkException("Output is invalid");
    }

    Optional<NotePlaintextEncryptionResult> res = output.getNote()
        .encrypt(output.getNote().getPkD());
    if (!res.isPresent()) {
      throw new ZksnarkException("Failed to encrypt note");
    }

    NotePlaintextEncryptionResult enc = res.get();
    NoteEncryption encryptor = enc.getNoteEncryption();

    byte[] cv = new byte[32];
    byte[] zkProof = new byte[192];
    if (!JLibrustzcash.librustzcashSaplingOutputProof(
        new OutputProofParams(ctx,
            encryptor.getEsk(),
            output.getNote().getD().getData(),
            output.getNote().getPkD(),
            output.getNote().getRcm(),
            output.getNote().getValue(),
            cv,
            zkProof))) {
      throw new ZksnarkException("Output proof failed");
    }
```
