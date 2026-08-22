## Title
Insufficiently domain-separated signature verification in the `ValidateMultiSign` TVM precompile enables cross-contract signature replay - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

## Summary
The `ValidateMultiSign` precompiled contract, exposed to every TVM smart contract at address `0x...a` as `validatemultisign(address,uint256,bytes32,bytes[])`, verifies whether a set of signatures over an arbitrary, caller-supplied `bytes32 data` value satisfies an account's permission threshold. It builds the signed message with only `owner_address || permissionId || data`, omitting any binding to the *calling contract* (`msg.sender`/receiver context) or chain identifier. This is the same untyped-data-signing root cause described in the Rigor report: since the precompile does not domain-separate by the DApp/contract consuming the signature, or by chain, a signature authorized for one smart contract's action can be replayed against any other smart contract that also calls `validatemultisign` with the same account/permission/`data` triple.

## Finding Description
`PrecompiledContracts.ValidateMultiSign.execute` at [1](#0-0)  constructs the message hash as:
```
combine = address || fromInt(permissionId) || data
hash = sha256(combine)
```
and then recovers signer addresses for `hash` and accumulates weights against the account's `Permission`, returning success once `totalWeight >= permission.getThreshold()` at [2](#0-1) .

The only "domain" fields folded into the hash are the target account address and its permission id — there is no inclusion of:
- The calling/receiving contract address (i.e., which DApp is consuming the approval), and
- Any chain identifier.

This mirrors exactly attack classes (2) and (3) from the referenced report: because `data` is a free-form 32-byte value chosen entirely by the calling contract's application logic, any two unrelated TVM contracts that call `validatemultisign` for the *same* controlling account/permission and happen to construct the same (or attacker-replayable) `data` value will accept each other's signatures. A signature that an account owner produced to authorize one DApp's action (e.g., a withdrawal approval in exchange contract A) is fully valid input to a different DApp (contract B) built on the same precompile, as long as B computes the same `address/permissionId/data` triple — nothing in the precompile itself prevents this, and the precompile's public documentation/ABI encourages exactly this generic "verify this account approved this data" usage pattern across independently deployed contracts. There is likewise no chain-id component, so the same replay is possible across different java-tron based networks (mainnet/testnets/private chains) sharing the same account address space.

Contrast with `BatchValidateSign` at [3](#0-2) , which is explicitly documented as a raw ecrecover-style batch primitive (comparable to Ethereum's `ecrecover`), where the lack of domain separation is an accepted, well-understood characteristic of the primitive. `ValidateMultiSign`, by contrast, already attempts partial domain separation (binding `address` and `permissionId`) — signaling an intent to make the precompile safely reusable across contexts — but stops short of binding to the calling contract or chain, leaving the same class of cross-application replay risk the report describes.

## Impact Explanation
Any smart contract on java-tron that relies on `ValidateMultiSign` to gate account-permissioned actions (fund releases, escrow settlement, exchange withdrawal approvals, DAO/multisig-style authorizations) is exposed to unauthorized action execution if a second, unrelated contract also consumes `validatemultisign` approvals for the same controlling account/permission and constructs `data` in a colliding or predictable way. An attacker who observes a signature intended for one DApp (signatures of this kind are typically transmitted off-chain to authorize a call, or are visible once included in a transaction) can submit it to a different contract to authorize an unintended operation, potentially resulting in unauthorized asset transfers or state changes — i.e., asset/accounting corruption reachable purely from ordinary contract calls, without any privileged access.

## Likelihood Explanation
Exploitation requires: (a) an account/permission that is used as the trust anchor for signature-based approvals in more than one independently-deployed smart contract (a realistic and even encouraged pattern for the precompile, e.g., custodial/exchange multisig accounts approving actions across multiple integrated contracts), and (b) the ability for an attacker to obtain a previously-produced valid signature and resubmit it to a different consuming contract. Because the precompile design does not force DApp developers to add their own domain separation, and nothing in the TVM/precompile layer warns against this reuse, likelihood is non-trivial for any ecosystem where multiple contracts share the same account-based authorization anchor.

## Recommendation
Extend the hashed payload inside `ValidateMultiSign.execute` to include a domain separator analogous to EIP-712: bind the hash to the calling contract address (the contract invoking the precompile, i.e., the "verifying contract"), and optionally the chain id, e.g.:
```
combine = callingContractAddress || chainId || address || permissionId || data
```
This prevents a signature approved for one contract/chain context from validating in another, while preserving backward-compatible signing UX for consumers who adopt the new hashing scheme behind a hard fork gate (similar to how `VMConfig.allowTvmSelfdestructRestriction()`/`allowTvmOsaka()` gate other behavior changes in this file, see [4](#0-3) ).

## Proof of Concept
Based on the existing test harness for this precompile (`ValidateMultiSignContractTest`), the collision can be demonstrated as follows:
1. Deploy two independent contracts, `ContractA` and `ContractB`, each of which calls `validatemultisign(ownerAddress, permissionId, data, signatures)` to gate its own unrelated action (e.g., `ContractA` releases funds, `ContractB` grants a role).
2. Have the account owner sign `data = sha256(ownerAddress || permissionId || applicationPayload)` intending to authorize `ContractA`'s action only.
3. Submit the same `signatures` and `data` to `ContractB`. Because the precompile hash in [5](#0-4)  never includes `ContractB`'s address, `ContractB.validatemultisign` call succeeds identically to `ContractA`'s, as demonstrated by the identical hash-construction path exercised in `ValidateMultiSignContractTest.testDifferentCase` at [6](#0-5) , which shows the exact same `merge(address, permissionId, data)` → sha256 → signature-check flow succeeding for any caller providing that triple, with no contract-context binding anywhere in the call path.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1051-1064)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1080-1119)
```java
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
        } catch (Throwable t) {
          if (t instanceof OutOfTimeException) {
            throw t;
          }
          logger.info("ValidateMultiSign error:{}", t.getMessage());
        }
      }
      return Pair.of(true, DATA_FALSE);
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L1123-1178)
```java
  public static class BatchValidateSign extends PrecompiledContract {

    private static final ExecutorService workers;
    private static final String workersName = "validate-sign-contract";
    private static final int ENGERYPERSIGN = 1500;
    private static final int MAX_SIZE = 16;
    private static final int ABI_HEADER_WORDS = 5;
    private static final int ABI_ITEM_WORDS = 6;

    static {
      workers = ExecutorServiceManager.newFixedThreadPool(workersName,
          Runtime.getRuntime().availableProcessors() / 2 + 1);
    }

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
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/ValidateMultiSignContractTest.java (L102-125)
```java
    //generate data

    byte[] address = key.getAddress();
    int permissionId = 2;
    byte[] data = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), longData);

    //combine data
    byte[] merged = ByteUtil.merge(address, ByteArray.fromInt(permissionId), data);
    //sha256 of it
    byte[] toSign = Sha256Hash.hash(CommonParameter
        .getInstance().isECKeyCryptoEngine(), merged);

    //sign data

    List<Object> signs = new ArrayList<>();
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    //add Repetitive
    signs.add(Hex.toHexString(key1.sign(toSign).toByteArray()));
    signs.add(Hex.toHexString(key2.sign(toSign).toByteArray()));

    Assert.assertArrayEquals(
        validateMultiSign(StringUtil.encode58Check(key.getAddress()), permissionId, data, signs)
            .getValue(), DataWord.ONE().getData());
```
