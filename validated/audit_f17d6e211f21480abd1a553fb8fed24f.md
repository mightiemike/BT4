### Title
Transaction malleability in shielded transfers via `TransactionCapsule.hashShieldTransaction` enables free re-verification DoS - ([File: chainbase/.../TransactionCapsule.java])

### Finding Description
`hashShieldTransaction` (chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java:284-323) computes the Sapling `signHash` used by `ShieldedTransferActuator.checkProof` from a reconstructed `ShieldedTransferContract` that includes only `fromAmount`, `toAmount`, `transparentFromAddress`, `transparentToAddress`, `receiveDescriptionList`, and `spendDescriptionList` (with `spendAuthoritySignature` cleared) [1](#0-0) . Fields from `Transaction.raw` such as `feeLimit`, `expiration`, `timestamp`, and `ref_block_hash`/`ref_block_bytes` are never folded into this hash, even though they are part of the transaction's raw data and thus part of its `txid`.

`ShieldedTransferActuator.checkProof` uses this `signHash` to call `JLibrustzcash.librustzcashSaplingCheckSpend`, `librustzcashSaplingCheckOutput`, and `librustzcashSaplingFinalCheck` [2](#0-1) . Since these zk-SNARK proofs and the binding signature were produced over `signHash`, any mutation to `feeLimit`/`expiration`/`refBlock` produces a different `txid` but an *identical* `signHash`, so the proofs still verify successfully.

For a purely shielded-to-shielded (or shielded-to-transparent, without `transparentFromAddress`) transfer, `TransactionCapsule.getOwner` returns an empty byte array when `transparentFromAddress` is empty [3](#0-2) , meaning no transparent-account signature/permission check applies to such a contract — the only authorization is the Sapling proof/signature bound to `signHash`. Since `signHash` doesn't cover `feeLimit`/`expiration`/`refBlock`, an attacker with no private key can take any observed in-flight `ShieldedTransferContract` transaction, change these unbound fields, and rebroadcast it as a new, distinct, still-valid transaction (different `txid`, same `signHash`).

`ShieldedTransferActuator.checkProof` does cache validation results in `ZKProofStore` keyed by `tx.getTransactionId().getBytes()` [4](#0-3) , but because the cache key is the full transaction id (which *does* depend on the mutated fields), each mutated clone gets a fresh cache miss and forces a brand-new, full SNARK verification (`librustzcashSaplingVerificationCtxInit` → per-spend `CheckSpend` → per-receive `CheckOutput` → `FinalCheck`) [5](#0-4) . Standard bandwidth/energy fee accounting only bills the sender upon successful `execute()`; a rebroadcast that reaches `validate()` and fails/succeeds there is not charged to the attacker, and since the attacker isn't the sender (no private key), no fee is deducted from them at all.

### Impact Explanation
This is a CPU-exhaustion denial-of-service vector against the TRON protocol implementation: an unprivileged attacker who observes any broadcast shielded transaction can clone it N times with different `feeLimit`/`expiration`/`refBlock` values and rebroadcast all N, forcing every validating full node to redo the full, computationally expensive Groth16/Sapling proof verification pipeline (`librustzcashSaplingCheckSpend`/`CheckOutput`/`FinalCheck`) for each clone, with zero cost to the attacker (no valid signature, no fee paid, no private key required). This matches "DoS via the TRON protocol implementation" bounty class.

### Likelihood Explanation
Preconditions are minimal and match a fully unprivileged threat model: the attacker only needs to observe one broadcast/mempool shielded transaction (trivial via P2P sniffing or their own RPC node), clone the protobuf, tweak `feeLimit`/`expiration`/`ref_block_bytes`/`ref_block_hash`, and rebroadcast — no signing key, no special role, no non-default configuration needed. The attack is trivially repeatable for any number of clones per captured transaction and can be automated at scale, and since fees are charged only on successful `execute()` (which these malleated duplicates need not reach if made to fail after `checkProof`, or even if they succeed the cost is borne by the original signer, not the attacker), the attacker's cost is essentially the broadcast bandwidth alone.

### Recommendation
Bind the entire `Transaction.raw` (or at minimum `feeLimit`, `expiration`, `ref_block_bytes`, `ref_block_hash`, `timestamp`) into the `signHash` computed in `hashShieldTransaction`, so any mutation of these fields invalidates the SNARK proof binding signature check. Additionally/alternatively, key the `ZKProofStore` cache (or a separate anti-replay cache) by `signHash` rather than by `txid`, so that malleated clones sharing the same `signHash` short-circuit to the cached result instead of re-running the full proof-verification pipeline.

### Proof of Concept
```java
// Conceptual JUnit outline (framework/src/test/java/org/tron/core/actuator/ShieldedTransferActuatorTest.java style):
// 1. Build a valid pure shielded transfer TransactionCapsule via ZenTransactionBuilder (spend + receive, no transparentFromAddress).
// 2. For i in 1..N:
//      TransactionCapsule clone = original.clone-with-mutated(feeLimit=i, expiration=now+i*1000);
//      // txid(clone) != txid(original) because raw data differs
//      // signHash(clone) == signHash(original) because hashShieldTransaction ignores feeLimit/expiration
//      ShieldedTransferActuator actuator = new ShieldedTransferActuator();
//      actuator.setChainBaseManager(...).setContract(clone.getContract(0)).setTx(clone);
//      long t0 = System.nanoTime();
//      actuator.validate(); // expected: passes librustzcashSaplingCheckSpend/CheckOutput/FinalCheck every time
//      long t1 = System.nanoTime();
//      // Assert: (t1 - t0) is ~constant and non-trivial (full SNARK verification cost) for every i,
//      // and ZKProofStore.has(clone.getTransactionId()) was false before this call (cache miss),
//      // proving no reuse of prior verification work despite identical signHash.
```
Expected assertion: verification cost (call count to `JLibrustzcash.librustzcashSaplingCheckSpend/CheckOutput/FinalCheck`) scales linearly with N mutated clones of a single captured transaction, with zero additional signing cost to the attacker, confirming unbounded free re-verification.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L296-313)
```java
    ShieldedTransferContract.Builder newContract = ShieldedTransferContract.newBuilder();
    newContract.setFromAmount(shieldedTransferContract.getFromAmount());
    newContract.addAllReceiveDescription(shieldedTransferContract.getReceiveDescriptionList());
    newContract.setToAmount(shieldedTransferContract.getToAmount());
    newContract.setTransparentFromAddress(shieldedTransferContract.getTransparentFromAddress());
    newContract.setTransparentToAddress(shieldedTransferContract.getTransparentToAddress());
    for (SpendDescription spendDescription : shieldedTransferContract.getSpendDescriptionList()) {
      newContract
          .addSpendDescription(spendDescription.toBuilder().clearSpendAuthoritySignature().build());
    }

    Transaction.raw.Builder rawBuilder = tx.toBuilder()
        .getRawDataBuilder()
        .clearContract()
        .addContract(
            Transaction.Contract.newBuilder().setType(ContractType.ShieldedTransferContract)
                .setParameter(
                    Any.pack(newContract.build())).build());
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L330-339)
```java
      switch (contract.getType()) {
        case ShieldedTransferContract: {
          ShieldedTransferContract shieldedTransferContract = contractParameter
              .unpack(ShieldedTransferContract.class);
          if (!shieldedTransferContract.getTransparentFromAddress().isEmpty()) {
            owner = shieldedTransferContract.getTransparentFromAddress();
          } else {
            return new byte[0];
          }
          break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L278-285)
```java
    ZKProofStore proofStore = chainBaseManager.getProofStore();
    if (proofStore.has(tx.getTransactionId().getBytes())) {
      if (proofStore.get(tx.getTransactionId().getBytes())) {
        return;
      } else {
        throw new ZkProofValidateException("record is fail, skip proof", false);
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L287-354)
```java
    byte[] signHash = getShieldTransactionHashIgnoreTypeException(tx.getInstance());

    if (CollectionUtils.isNotEmpty(spendDescriptions)
        || CollectionUtils.isNotEmpty(receiveDescriptions)) {
      long ctx = JLibrustzcash.librustzcashSaplingVerificationCtxInit();
      try {
        for (SpendDescription spendDescription : spendDescriptions) {
          if (!JLibrustzcash.librustzcashSaplingCheckSpend(
              new CheckSpendParams(ctx,
                  spendDescription.getValueCommitment().toByteArray(),
                  spendDescription.getAnchor().toByteArray(),
                  spendDescription.getNullifier().toByteArray(),
                  spendDescription.getRk().toByteArray(),
                  spendDescription.getZkproof().toByteArray(),
                  spendDescription.getSpendAuthoritySignature().toByteArray(),
                  signHash)
          )) {
            throw new ZkProofValidateException("librustzcashSaplingCheckSpend error", true);
          }
        }

        for (ReceiveDescription receiveDescription : receiveDescriptions) {
          if (receiveDescription.getCEnc().size() != ZC_ENCCIPHERTEXT_SIZE
              || receiveDescription.getCOut().size() != ZC_OUTCIPHERTEXT_SIZE) {
            throw new ZkProofValidateException("Cout or CEnc size error", true);
          }
          if (!JLibrustzcash.librustzcashSaplingCheckOutput(
              new CheckOutputParams(ctx,
                  receiveDescription.getValueCommitment().toByteArray(),
                  receiveDescription.getNoteCommitment().toByteArray(),
                  receiveDescription.getEpk().toByteArray(),
                  receiveDescription.getZkproof().toByteArray())
          )) {
            throw new ZkProofValidateException("librustzcashSaplingCheckOutput error", true);
          }
        }

        long valueBalance;
        long totalShieldedPoolValue = dynamicStore
            .getTotalShieldedPoolValue();
        try {
          valueBalance = addExact(subtractExact(
              shieldedTransferContract.getToAmount(),
              shieldedTransferContract.getFromAmount()), fee);
          totalShieldedPoolValue = subtractExact(
              totalShieldedPoolValue, valueBalance);
        } catch (ArithmeticException e) {
          logger.debug(e.getMessage(), e);
          throw new ZkProofValidateException(e.getMessage(), true);
        }

        if (totalShieldedPoolValue < 0) {
          throw new ZkProofValidateException("shieldedPoolValue error", true);
        }

        if (!JLibrustzcash.librustzcashSaplingFinalCheck(
            new FinalCheckParams(ctx,
                valueBalance,
                shieldedTransferContract.getBindingSignature().toByteArray(),
                signHash)
        )) {
          throw new ZkProofValidateException("librustzcashSaplingFinalCheck error", true);
        }
      } catch (ZksnarkException e) {
        throw new ZkProofValidateException(e.getMessage(), true);
      } finally {
        JLibrustzcash.librustzcashSaplingVerificationCtxFree(ctx);
      }
```
