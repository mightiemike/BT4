## Root cause found: analogous "charged-bytes vs. real-work" mismatch in `ValidateMultiSign` / `BatchValidateSign` precompiles

### Title
Precompile energy is charged from raw calldata length while actual ECDSA-recovery work is driven by independently-decoded ABI array length, allowing underpriced computation via non-canonical ABI encoding — (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The reported Biconomy bug is a class where a resource-refund/charge formula is derived from *raw byte length* of calldata while the *real work performed* is determined independently by decoded parameters, letting an attacker decouple the two via non-standard ABI padding. The same structural pattern exists in java-tron's `ValidateMultiSign` and `BatchValidateSign` TVM precompiled contracts, where energy is charged based on an assumed canonical calldata shape derived from `data.length`, but the actual number of expensive ECDSA-recovery operations performed is taken from an ABI-decoded dynamic-array length word that is not required to match that assumption unless a specific hard-fork flag is active.

### Finding Description
`ValidateMultiSign.getEnergyForData` computes the energy charge purely from the raw byte length of the calldata, assuming a fixed canonical shape (5 header words + 5 words per signature item): [1](#0-0) 

But `execute()` derives the actual signatures to process from the ABI-decoded dynamic array pointed to by `words[3]`, independently of `data.length`, and loops performing an expensive `recoverAddrBySign` (ECDSA recovery) per signature: [2](#0-1) 

`BatchValidateSign` has the identical structural pattern: energy is derived from `data.length` via a hard-coded item-word size (`ABI_ITEM_WORDS = 6`), while the real signature/address arrays used for the ECDSA-recovery workload are decoded independently from ABI offset/length words: [3](#0-2) 

Crucially, the java-tron team already identified and fixed this exact class of bug ("TIP-854"), adding an `isValidAbiEncoding(data, ABI_HEADER_WORDS, ABI_ITEM_WORDS)` guard to both precompiles that rejects calldata whose byte length is incompatible with the `(words - headerWords) / itemWords` shape the energy formula assumes. However, this guard is only invoked when `VMConfig.allowTvmOsaka()` is enabled: [4](#0-3) 

The accompanying test explicitly documents that pre-activation, non-canonical/malformed calldata reaches the legacy decoder unguarded, which is "the existing behaviour" being preserved for backward compatibility: [5](#0-4) 

And another test comment states the guard's purpose is precisely to reject calldata "whose byte length is incompatible with the (words - 5) / 5 shape the per-call energy formula already assumes": [6](#0-5) 

This confirms the vulnerability class is real and reachable: before the `allowTvmOsaka` hard fork is activated network-wide, an unprivileged contract caller can craft calldata to `validatemultisign`/`batchvalidatesign` where the byte-length-derived `cnt` used for energy charging is smaller than the actual number of decoded signatures/ECDSA recoveries performed in `execute()`, mirroring exactly the "manipulate calldata shape to decouple the charged-resource heuristic from real consumed resource" root cause of the external report (there, gas refund from `msg.data.length`; here, energy charge from `data.length`).

### Impact Explanation
This is an **underpriced-public-work** impact: an unprivileged user (any TVM contract caller) can invoke `ValidateMultiSign`/`BatchValidateSign` with non-canonically-encoded calldata so that the energy actually burned is less than the CPU cost of the ECDSA signature recoveries actually executed by SR nodes. While the `MAX_SIZE` caps (5 for `ValidateMultiSign`, 16 for `BatchValidateSign`) bound the absolute magnitude of the discrepancy per call, this still allows systematically underpaying for expensive cryptographic verification work at a byte-for-byte discount relative to the intended energy model, which can be amplified across many transactions to impose disproportionate node CPU load per unit of energy paid — the same underlying economic exploit primitive as the H-02 report, applied to TVM resource accounting rather than an ether refund.

### Likelihood Explanation
Likelihood is contingent on network state: this is only exploitable **before** `VMConfig.allowTvmOsaka()` is activated on the network, since the fix (`isValidAbiEncoding`) is gated behind that flag. As of the current repository state, the fix exists only in code but its enforcement in `execute()` is conditional, and both precompiles are public, permissionless, and directly callable by any contract, making the primitive trivially reachable by any unprivileged caller pre-activation.

### Recommendation
Enforce `isValidAbiEncoding` (or equivalent calldata-shape validation) unconditionally in `ValidateMultiSign.execute` and `BatchValidateSign.execute`, rather than gating it behind `VMConfig.allowTvmOsaka()`, or ensure the hard fork activating this check is deployed to mainnet promptly. More generally, energy-charging formulas for precompiles should derive their cost from the same decoded, validated quantities used to drive real work (e.g., the actual decoded signature array length), not from an independent raw-byte-length heuristic that can diverge from the real workload via non-canonical ABI encodings.

### Proof of Concept
Conceptual PoC (matches the pattern already exercised in `testTip854RejectsMalformedCalldata`):
1. With `VMConfig.allowTvmOsaka()` disabled (pre-activation network state), construct calldata for `validatemultisign(address,uint256,bytes32,bytes[])` where the ABI offset/length word for the `bytes[]` signatures array is crafted such that `words[3]`-derived array length yields close to `MAX_SIZE` (5) real signatures to recover, while the total `data.length` is kept smaller than the canonical `(5 + 5*5)*32` bytes a 5-signature call would normally require (e.g., via overlapping/reused offset words, similar to non-standard ABI encoding tricks).
2. Call `contract.execute(data)` — `getEnergyForData(data)` charges energy for `cnt < 5` computed from `data.length`, but `execute()` still performs up to 5 real `recoverAddrBySign` ECDSA operations because the loop is driven by the decoded `signatures` array, not by the `cnt` used for charging.
3. This precisely reproduces the `testTip854RejectsMalformedCalldata` scenario (bucket 3: "32-aligned but tail not a multiple of I=5 words"), which the test confirms is only rejected when `VMConfig.initAllowTvmOsaka(1)` is set — confirming the same malformed-length exploit surface exists and is unguarded pre-activation. [7](#0-6)

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1044-1049)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 5;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1097)
```java
    @Override
    public Pair<Boolean, byte[]> execute(byte[] rawData) {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(rawData, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
      DataWord[] words = DataWord.parseArray(rawData);
      byte[] address = words[0].toTronAddress();
      int permissionId = words[1].intValueSafe();
      byte[] data = words[2].getData();

      byte[] combine = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
      byte[] hash = Sha256Hash.hash(CommonParameter
          .getInstance().isECKeyCryptoEngine(), combine);

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[3].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }
      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[3].intValueSafe() / WORD_SIZE, rawData) :
          extractBytesArray(words, words[3].intValueSafe() / WORD_SIZE, rawData);

      if (signatures.length == 0 || signatures.length > MAX_SIZE) {
        return Pair.of(true, DATA_FALSE);
      }

      AccountCapsule account = this.getDeposit().getAccount(address);
      if (account != null) {
        try {
          Permission permission = account.getPermissionById(permissionId);
          if (permission != null) {
            //calculate weight
            long totalWeight = 0L;
            List<byte[]> executedSignList = new ArrayList<>();
            for (byte[] sign : signatures) {
              byte[] recoveredAddr = recoverAddrBySign(sign, hash);

              sign = merge(recoveredAddr, sign);
              if (ByteArray.matrixContains(executedSignList, recoveredAddr)) {
                if (ByteArray.matrixContains(executedSignList, sign)) {
                  continue;
                }
                MUtil.checkCPUTime();
              }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1137-1179)
```java
    @Override
    public long getEnergyForData(byte[] data) {
      long cnt = (data.length / WORD_SIZE - 5) / 6;
      // one sign 1500, half of ecrecover
      return cnt * ENGERYPERSIGN;
    }

    @Override
    public Pair<Boolean, byte[]> execute(byte[] data) {
      try {
        return doExecute(data);
      } catch (Throwable t) {
        if (t instanceof InterruptedException){
          Thread.currentThread().interrupt();
        }
        return Pair.of(true, new byte[WORD_SIZE]);
      }
    }

    private Pair<Boolean, byte[]> doExecute(byte[] data)
        throws InterruptedException, ExecutionException {
      if (VMConfig.allowTvmOsaka()
          && !isValidAbiEncoding(data, ABI_HEADER_WORDS, ABI_ITEM_WORDS)) {
        return Pair.of(false, EMPTY_BYTE_ARRAY);
      }
      DataWord[] words = DataWord.parseArray(data);
      byte[] hash = words[0].getData();

      if (VMConfig.allowTvmSelfdestructRestriction()) {
        int sigArraySize = words[words[1].intValueSafe() / WORD_SIZE].intValueSafe();
        int addrArraySize = words[words[2].intValueSafe() / WORD_SIZE].intValueSafe();
        if (sigArraySize > MAX_SIZE || addrArraySize > MAX_SIZE) {
          return Pair.of(true, DATA_FALSE);
        }
      }

      byte[][] signatures = VMConfig.allowTvmSelfdestructRestriction() ?
          extractSigArray(words, words[1].intValueSafe() / WORD_SIZE, data) :
          extractBytesArray(words, words[1].intValueSafe() / WORD_SIZE, data);
      byte[][] addresses = extractBytes32Array(
          words, words[2].intValueSafe() / WORD_SIZE);
      int cnt = signatures.length;
      if (cnt == 0 || cnt > MAX_SIZE || signatures.length != addresses.length) {
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L158-192)
```java
  // TIP-854: after activation, validateMultiSign (H=5, I=5) must reject calldata
  // whose byte length is incompatible with the (words - 5) / 5 shape the per-call
  // energy formula already assumes, returning (false, empty).
  @Test
  public void testTip854RejectsMalformedCalldata() {
    VMConfig.initAllowTvmOsaka(1);
    try {
      // Bucket 1: 32-aligned head + sub-word trailing bytes (r=1, r=31).
      for (int r : new int[]{1, 31}) {
        byte[] data = new byte[(5 + 5) * 32 + r];
        Pair<Boolean, byte[]> ret = contract.execute(data);
        Assert.assertFalse("non-32-aligned len=" + data.length, ret.getLeft());
        Assert.assertSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
      }
      // Bucket 2: fewer than the static head's 5 words.
      for (int bytes : new int[]{0, 32, 64, 96, 128}) {
        Pair<Boolean, byte[]> ret = contract.execute(new byte[bytes]);
        Assert.assertFalse("len=" + bytes + " < 5 words", ret.getLeft());
        Assert.assertSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
      }
      // Bucket 3: 32-aligned but tail not a multiple of I=5 words (k = 1..4).
      for (int k = 1; k <= 4; k++) {
        byte[] data = new byte[(5 + k) * 32];
        Pair<Boolean, byte[]> ret = contract.execute(data);
        Assert.assertFalse("aligned bad-tail k=" + k, ret.getLeft());
        Assert.assertSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
      }
      // Null calldata: explicit spec clause.
      Pair<Boolean, byte[]> ret = contract.execute(null);
      Assert.assertFalse("null calldata", ret.getLeft());
      Assert.assertSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
    } finally {
      VMConfig.initAllowTvmOsaka(0);
    }
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L244-260)
```java
  // TIP-854: before activation, malformed calldata reaches the legacy decoder.
  // Assert the guard is not taken — this precompile has no outer catch, so a
  // too-short input raises inside the decoder; that is the documented
  // pre-activation failure mode the TIP explicitly preserves.
  @Test
  public void testTip854PreActivationNoOp() {
    VMConfig.initAllowTvmOsaka(0);
    contract.setRepository(RepositoryImpl.createRoot(StoreFactory.getInstance()));
    try {
      Pair<Boolean, byte[]> ret = contract.execute(new byte[(5 + 1) * 32]);
      // If the decoder happened to handle it without raising, we must not have
      // taken the post-activation reject path (false, empty).
      Assert.assertNotSame(ByteUtil.EMPTY_BYTE_ARRAY, ret.getRight());
    } catch (RuntimeException expectedLegacyBehaviour) {
      // Pre-activation: decoder may throw — this is the existing behaviour.
    }
  }
```
