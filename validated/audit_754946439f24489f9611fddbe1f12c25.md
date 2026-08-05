Based on my research, I found a strong, concrete analog to the reNFT hook-disabling DoS in java-tron's shielded TRC20 mechanism.

### Title
DoS of shielded TRC20 note spending via reversible `ALLOW_SHIELDED_TRC20_TRANSACTION` toggle - (File: `actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java`)

### Summary
The `ALLOW_SHIELDED_TRC20_TRANSACTION` committee-controlled feature flag gates access to the TVM precompiles (`verifyMintProof`, `verifyTransferProof`, `verifyBurnProof`, `merkleHash`) that a `ShieldedTRC20` contract's Solidity code must call to `mint`, `transfer`, or `burn` shielded notes. Unlike most feature flags in `ProposalUtil` (which can only ever be set to `1`, i.e., are one-way and irreversible), this flag is explicitly validated as reversible (`value != 1 && value != 0`), meaning the committee can disable it after users have already minted/locked funds into the shielded pool.

### Finding Description
`ProposalUtil.validator` validates `ALLOW_SHIELDED_TRC20_TRANSACTION` as accepting either `0` or `1`: [1](#0-0) 

This is in contrast to most other feature-activation proposals (e.g. `ALLOW_PBFT`, `ALLOW_TVM_ISTANBUL`, `ALLOW_MARKET_TRANSACTION`) which enforce `value != 1` throws, i.e. can only be turned on once and never reversed: [2](#0-1) 

`PrecompiledContracts.getContractForAddress` gates all of the shielded-TRC20 verification precompiles behind `VMConfig.allowShieldedTRC20Transaction()`, which reflects that same flag: [3](#0-2) 

`DynamicPropertiesStore.supportShieldedTRC20Transaction()` reads the flag as a simple boolean gate with no distinction between "creating new state" and "closing/exiting existing state": [4](#0-3) 

This is structurally identical to the reNFT bug: a user mints shielded notes (locking TRC20 tokens into the shielded pool contract) while the flag is `1`. If the committee later disables the flag (`0`), any transaction attempting to call `verifyBurnProof` (needed to `burn`/withdraw a note back to a transparent TRC20 balance) or `verifyTransferProof` (needed to move/spend a note) will find the precompile unavailable/disabled, since `getContractForAddress` returns `null`/falls through when `allowShieldedTRC20Transaction()` is false. The ability to spend a note is bound to the same flag as the ability to create the note — exactly the pattern flagged in the reNFT finding where `onStart` and `onStop` were coupled to the same togglable hook status.

### Impact Explanation
If the committee disables `ALLOW_SHIELDED_TRC20_TRANSACTION` while shielded notes are outstanding, every unspent note becomes permanently unspendable: the shielded contract's `burn`/`transfer` functions rely on the VM precompiles that no longer resolve. TRC20 tokens locked in the shielded pool contract become stuck/inaccessible, matching the "assets remain stuck" impact class from the original finding.

### Likelihood Explanation
This requires a privileged committee/witness proposal action to disable the flag, similar to the reNFT bug requiring an admin action (`Guard::updateHookStatus`) — hence it is a "governance-triggered" DoS rather than a permissionless exploit. It's plausible as an operational mistake (e.g., disabling the feature due to a discovered cryptographic bug, as historically happened with the original `ALLOW_SHIELDED_TRANSACTION`, whose case is now commented out in `ProposalUtil`) while active shielded TRC20 deployments still hold locked value.

### Recommendation
Decouple "create" and "close/exit" operations from the same feature flag, mirroring reNFT's fix: once notes have been minted under the flag being enabled, spending/burning operations (`verifyTransferProof`, `verifyBurnProof`) should remain available regardless of the current flag state, while only `mint`/new-note-creation paths (`verifyMintProof`) should be gated by the flag. Alternatively, make the flag one-way only (never revertible to `0`), consistent with the pattern used for most other feature-activation `ProposalType`s in `ProposalUtil.java`.

### Proof of Concept
1. Committee proposes and passes `ALLOW_SHIELDED_TRC20_TRANSACTION = 1` (per `ProposalUtil.validator`, case at [1](#0-0) ).
2. A user mints shielded notes via a `ShieldedTRC20` contract, which internally calls the `verifyMintProof` precompile (enabled by the flag) as shown in tests such as [5](#0-4) .
3. Committee later proposes `ALLOW_SHIELDED_TRC20_TRANSACTION = 0`, which is accepted per the same validator branch (reversible flag).
4. `VMConfig.allowShieldedTRC20Transaction()` now returns `false`, so `PrecompiledContracts.getContractForAddress` no longer resolves `verifyBurnProofAddr`/`verifyTransferProofAddr` ( [3](#0-2) ).
5. The user's attempt to call `burn` (or `transfer`) on the shielded contract to reclaim their locked TRC20 tokens fails because the underlying precompile call is unavailable, permanently locking the previously minted value.

Note: I was unable to fully trace the exact runtime failure mode (revert vs. no-op) inside the compiled `ShieldedTRC20` Solidity bytecode itself when the precompile call target is missing, since that contract's bytecode/ABI logic sits outside the indexed Java sources. A Devin session with full repo/tooling access could confirm the exact on-chain failure behavior by deploying/testing the `ShieldedTRC20` contract against a toggled flag.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L325-345)
```java
      case ALLOW_PBFT: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_PBFT]");
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_PBFT] is only allowed to be 1");
        }
        break;
      }
      case ALLOW_TVM_ISTANBUL: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_1)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_TVM_ISTANBUL]");
        }
        if (value != 1) {
          throw new ContractValidateException(
              "This value[ALLOW_TVM_ISTANBUL] is only allowed to be 1");
        }
        break;
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L347-357)
```java
      case ALLOW_SHIELDED_TRC20_TRANSACTION: {
        if (!forkController.pass(ForkBlockVersionEnum.VERSION_4_0_1)) {
          throw new ContractValidateException(
              "Bad chain parameter id [ALLOW_SHIELDED_TRC20_TRANSACTION]");
        }
        if (value != 1 && value != 0) {
          throw new ContractValidateException(
              "This value[ALLOW_SHIELDED_TRC20_TRANSACTION] is only allowed to be 1 or 0");
        }
        break;
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/PrecompiledContracts.java (L260-271)
```java
    if (VMConfig.allowShieldedTRC20Transaction() && address.equals(verifyMintProofAddr)) {
      return verifyMintProof;
    }
    if (VMConfig.allowShieldedTRC20Transaction() && address.equals(verifyTransferProofAddr)) {
      return verifyTransferProof;
    }
    if (VMConfig.allowShieldedTRC20Transaction() && address.equals(verifyBurnProofAddr)) {
      return verifyBurnProof;
    }
    if (VMConfig.allowShieldedTRC20Transaction() && address.equals(merkleHashAddr)) {
      return merkleHash;
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2081-2083)
```java
  public boolean supportShieldedTRC20Transaction() {
    return getAllowShieldedTRC20Transaction() == 1L;
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/PrecompiledContractsVerifyProofTest.java (L756-799)
```java
  @Test
  public void verifyBurnProofCorrect() throws ZksnarkException {
    int totalCountNum = 2;
    long leafCount = 0;
    long value = 100L;
    byte[] frontier = new byte[32 * 33];

    IncrementalMerkleTreeContainer tree = new IncrementalMerkleTreeContainer(
        new IncrementalMerkleTreeCapsule());
    for (int countNum = 0; countNum < totalCountNum; countNum++) {
      SpendingKey senderSk = SpendingKey.random();
      ExpandedSpendingKey senderExpsk = senderSk.expandedSpendingKey();
      FullViewingKey senderFvk = senderSk.fullViewingKey();
      IncomingViewingKey senderIvk = senderFvk.inViewingKey();
      byte[] rcm = new byte[32];
      JLibrustzcash.librustzcashSaplingGenerateR(rcm);
      PaymentAddress senderPaymentAddress = senderIvk.address(DiversifierT.random()).orElse(null);
      assertNotNull(senderPaymentAddress);
      { //for mint
        ShieldedTRC20ParametersBuilder mintBuilder = new ShieldedTRC20ParametersBuilder();
        mintBuilder.setTransparentFromAmount(BigInteger.valueOf(value));
        mintBuilder.setShieldedTRC20Address(SHIELDED_CONTRACT_ADDRESS);
        mintBuilder.setShieldedTRC20ParametersType(ShieldedTRC20ParametersType.MINT);
        mintBuilder.addOutput(DEFAULT_OVK, senderPaymentAddress.getD(),
            senderPaymentAddress.getPkD(), value, rcm, new byte[512]);
        ShieldedTRC20Parameters mintParams = mintBuilder.build(false);

        byte[] mintInputData = abiEncodeForMint(mintParams, value, frontier, leafCount);
        Pair<Boolean, byte[]> mintContractResult = mintContract.execute(mintInputData);
        byte[] mintResult = mintContractResult.getRight();
        Assert.assertEquals(1, mintResult[31]);

        //update frontier and leafCount
        //if slot == 0, frontier[0:31]=noteCommitment
        int slot = mintResult[63];
        if (slot == 0) {
          System.arraycopy(mintInputData, 0, frontier, 0, 32);
        } else {
          int srcPos = (slot + 1) * 32;
          int destPos = slot * 32;
          System.arraycopy(mintResult, srcPos, frontier, destPos, 32);
        }
        leafCount++;
      }
```
