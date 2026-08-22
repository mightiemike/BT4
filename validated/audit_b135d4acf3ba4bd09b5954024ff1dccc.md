### Title
`ValidateMultiSign` TVM precompile performs stateless signature-weight checks with no nonce or consumption tracking, letting any observer front-run/replay same-account authorization signatures used by permit-like DeFi flows - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The Tapioca finding describes a class of bugs where "permits" (bearer signatures usable by any `msg.sender`) allow anyone monitoring the chain to extract and rebroadcast grant/revoke signatures out of the intended order, deterministically breaking downstream flows that assume sequential application. `java-tron`'s TVM exposes an analogous primitive to every smart contract: the `ValidateMultiSign` precompiled contract at address `0x...a` [1](#0-0) . It is a pure, stateless signature-weight verifier that any contract can call to build custom "permit"/allowance authorization schemes, but it neither consumes signatures nor enforces any nonce, so any DApp built on it inherits exactly the bearer-signature reordering/reuse problem described in the report.

### Finding Description
`ValidateMultiSign.execute()` takes an `address`, `permissionId`, an arbitrary caller-supplied `data` blob, and a set of signatures, then computes `hash = Sha256(address || permissionId || data)` and checks that the supplied signatures recover to keys in the account's `Permission` whose combined weight is `>= threshold` [2](#0-1) . This check:
- Is purely functional — for the same `(address, permissionId, data, signatures)` tuple, the result is always the same, and it can be invoked with **any signature copy an observer has seen**, from **any caller/contract**, at **any time**, since the precompile does not read/write any persistent "used-signature" store.
- Performs no expiry, nonce, or "already consumed" bookkeeping at the VM level; any replay/ordering protection must be implemented entirely by the calling contract via the content of `data`.

This mirrors the underlying `TransactionCapsule.checkWeight`/`getWeight` permission-weight logic used for on-chain multi-sig transactions [3](#0-2) , but unlike a full transaction (which is bound to TaPos, expiration, and a single consensus-ordered execution), the precompile is invoked from inside arbitrary TVM contract logic with no built-in binding to execution order, consumption, or replay protection.

Because Tron contract developers commonly reuse `ValidateMultiSign` (guarded only by `VMConfig.allowTvmSolidity059()`) to implement account-abstraction-style permit/allowance verification for TRC20-like tokens without going through full signed transactions [4](#0-3) , any contract that signs two logically-sequenced authorization messages against the same account/permissionId (e.g., "grant allowance" and later "revoke allowance") produces two independently-valid bearer signatures. Since the precompile does not invalidate a signature once verified, and verification is available to any `TriggerSmartContract` caller who has observed the signature in the mempool or in a prior on-chain call, an attacker can:
- Front-run the intended ordering by broadcasting the "revoke" signature before the "grant" signature is confirmed, or vice versa.
- Repeatedly resubmit (replay) the *same* previously-used signature in unrelated future calls if the contract's own `data` payload does not encode a strictly-consumed nonce, since the precompile provides no such enforcement.

### Impact Explanation
Any dApp/token contract deployed on TVM that relies on `ValidateMultiSign` to implement grant/revoke allowance-style permit flows is exposed to the same "cannot be solved via chosen standard" MEV/DoS griefing described in the original report: legitimate multi-step authorization sequences (grant then use then revoke) can be permanently broken by a third party who only needs to observe the signed payloads in the mempool, without needing any private key or special privilege. Additionally, because the precompile has no consumption tracking, contracts that fail to embed proper nonces in `data` are further exposed to outright signature replay (not just reordering), amplifying the impact to unauthorized repeated execution of the signed operation.

### Likelihood Explanation
This is reachable from any unprivileged `TriggerSmartContract` call (a normal contract invocation) against any deployed contract that uses the `validatemultisign` precompile address for its authorization logic — no special key leakage, no privileged actor, and no node/P2P assumptions are required; the attacker only needs standard mempool visibility, matching the report's threat model precisely.

### Recommendation
- Document clearly (and, where feasible, enforce at the precompile boundary) that `ValidateMultiSign` provides no replay or ordering protection, and require calling contracts to embed monotonically increasing nonces and expiration timestamps inside `data`, with an explicit "used signature/nonce" mapping tracked in contract storage.
- For allowance-style flows built on this precompile, recommend the strict `0 -> X -> 0` allowance pattern (mirroring the Tapioca report's suggested `renounceAllowance` mitigation) so that "revoke" is re-derived from current on-chain state rather than from an independently pre-signed bearer message that can be reordered relative to a "grant" message.
- Consider adding a companion precompile/opcode that atomically marks a signature (or its hash) as consumed on first successful verification, so `ValidateMultiSign`-based flows gain native replay protection instead of relying purely on contract-author discipline.

### Proof of Concept
1. Deploy a token/allowance contract on TVM that uses `ValidateMultiSign` (address `0x...a`) to authorize `approve(spender, amount)` and `revoke(spender)` calls, where `data` passed to the precompile encodes only `(spender, amount)`/`(spender)` without a strictly consumed nonce.
2. Account owner signs `sig_grant` over `data = approve(Bob, 100)` and later signs `sig_revoke` over `data = revoke(Bob)`, intending `sig_grant` to be applied first and `sig_revoke` applied only after Bob's intended usage completes.
3. An attacker monitoring the mempool observes both signed payloads (submitted as ordinary `TriggerSmartContract` calls carrying the raw signature bytes) and re-submits `sig_revoke`'s call ahead of/immediately after `sig_grant`'s call via a higher-fee transaction, exploiting that `ValidateMultiSign.execute()` (`PrecompiledContracts.java:1051-1119`) has no state to prevent this reordering or to mark `sig_grant`/`sig_revoke` as consumed.
4. Result: Bob's intended usage of the allowance reverts/fails because the revoke was applied out of order, or `sig_grant`/`sig_revoke` can be replayed again in a subsequent unrelated block, since the precompile itself never records that the signature was already acted upon.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L149-151)
```java
  private static final DataWord batchValidateSignAddr = new DataWord(
      "0000000000000000000000000000000000000000000000000000000000000009");
  private static final DataWord validateMultiSignAddr = new DataWord(
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L254-259)
```java
    if (VMConfig.allowTvmSolidity059() && address.equals(batchValidateSignAddr)) {
      return batchValidateSign;
    }
    if (VMConfig.allowTvmSolidity059() && address.equals(validateMultiSignAddr)) {
      return validateMultiSign;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1111)
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
              long weight = TransactionCapsule.getWeight(permission, recoveredAddr);
              if (weight == 0) {
                //incorrect sign
                return Pair.of(true, DATA_FALSE);
              }
              totalWeight += weight;
              executedSignList.add(sign);
              executedSignList.add(recoveredAddr);
            }

            if (totalWeight >= permission.getThreshold()) {
              return Pair.of(true, dataOne());
            }
          }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/TransactionCapsule.java (L218-270)
```java
  public static long getWeight(Permission permission, byte[] address) {
    List<Key> list = permission.getKeysList();
    for (Key key : list) {
      if (key.getAddress().equals(ByteString.copyFrom(address))) {
        return key.getWeight();
      }
    }
    return 0;
  }

  /**
   *  make sure ForkController.init(ChainBaseManager) is invoked before invoke this method.
   *
   *  @see ForkController#init(org.tron.core.ChainBaseManager)
   */
  public static long checkWeight(Permission permission, List<ByteString> sigs, byte[] hash,
      List<ByteString> approveList)
      throws SignatureException, PermissionException, SignatureFormatException {
    long currentWeight = 0;
    if (sigs.size() > permission.getKeysCount()) {
      throw new PermissionException(
          "Signature count is " + (sigs.size()) + " more than key counts of permission : "
              + permission.getKeysCount());
    }
    HashMap addMap = new HashMap();
    for (ByteString sig : sigs) {
      if (sig.size() < 65) {
        throw new SignatureFormatException(
            "Signature size is " + sig.size());
      }
      String base64 = TransactionCapsule.getBase64FromByteString(sig);
      byte[] address = SignUtils
          .signatureToAddress(hash, base64, CommonParameter.getInstance().isECKeyCryptoEngine());
      long weight = getWeight(permission, address);
      if (weight == 0) {
        throw new PermissionException(
            ByteArray.toHexString(hash) + " is signed by " + encode58Check(address)
                + " but it is not contained of permission.");
      }
      if (ForkController.instance().pass(Parameter.ForkBlockVersionEnum.VERSION_4_7_1)) {
        base64 = encode58Check(address);
      }
      if (addMap.containsKey(base64)) {
        throw new PermissionException(encode58Check(address) + " has signed twice!");
      }
      addMap.put(base64, weight);
      if (approveList != null) {
        approveList.add(ByteString.copyFrom(address)); //out put approve list.
      }
      currentWeight += weight;
    }
    return currentWeight;
  }
```
