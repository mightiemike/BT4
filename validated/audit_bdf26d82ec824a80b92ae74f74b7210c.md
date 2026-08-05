### Title
Unauthenticated pre-proof anchor validation missing in `createShieldedTransactionWithoutSpendAuthSig` enables underpriced zk-proof CPU DoS - (File: `framework/src/main/java/org/tron/core/services/http/CreateShieldedTransactionWithoutSpendAuthSigServlet.java`)

### Summary
`CreateShieldedTransactionWithoutSpendAuthSigServlet.doPost` forwards attacker-supplied `PrivateParametersWithoutAsk` directly to `Wallet.createShieldedTransactionWithoutSpendAuthSig`, which unconditionally builds a Groth16 Sapling spend proof for every supplied `SpendNote` via `ZenTransactionBuilder.generateSpendProof`/`librustzcashSaplingSpendProof`, without first checking that `voucher.rt` (the anchor) exists in `MerkleContainer`. The anchor-existence check (`merkleContainer.merkleRootExist(...)`, throwing "Rt is invalid.") only exists in `ShieldedTransferActuator.validate()`, a separate step invoked only when the resulting transaction is actually broadcast/pushed — not during this create-only API call.

### Finding Description
The servlet is only gated by rate limiting (`RateLimiterServlet`) and requires no authentication or ownership of real funds: [1](#0-0) 

`Wallet.createShieldedTransactionWithoutSpendAuthSig` only checks `checkAllowShieldedTransactionApi()` (a global config flag) and basic input-format validation (`checkCmValid`), then for each `SpendNote` builds an `IncrementalMerkleVoucherContainer` purely from client-supplied bytes and calls `builder.addSpend(..., spendNote.getVoucher().getRt().toByteArray(), voucherContainer)` — there is no lookup into `MerkleContainer`/`incrementalMerkleTreeStore` to confirm the supplied `rt` (anchor) is a root the node actually knows about: [2](#0-1) 

`builder.buildWithoutAsk()` then runs `generateSpendProof` for each spend, which calls the native `librustzcashSaplingSpendProof` (Groth16 proving, CPU-expensive) using only the client-provided note data, alpha, anchor, and Merkle path — this succeeds as long as the path/tree data is internally well-formed, regardless of whether that anchor was ever committed to the chain: [3](#0-2) 

The only place the anchor is checked against the node's actual state is in `ShieldedTransferActuator.validate()`, executed later during transaction broadcast/execution, not during this proof-building API call: [4](#0-3) 

Because an attacker can locally construct their own arbitrary `IncrementalMerkleVoucherCapsule`/tree (using self-chosen commitments and their own `ak`/`nsk`/`ovk`), they never need any real spendable note or knowledge of on-chain state — they only need internally-consistent Merkle path bytes, which is trivial to fabricate offline. This is exactly the "wrong anchor" scenario the codebase's own test `TestWrongAnchor` demonstrates fails only at `ShieldedTransferActuator.validate()` time with "Rt is invalid.", which is after proof generation, not before it: [5](#0-4) 

### Impact Explanation
Each HTTP POST to `/wallet/createshieldedtransactionwithoutspendauthsig` with a fabricated anchor/voucher forces the node to perform a full Sapling Groth16 spend-proof computation (native, CPU-heavy cryptographic operation) before any cheap validation (anchor existence) is applied. This is a classic underpriced-work pattern: the attacker's cost is a small serialized voucher and note payload; the node's cost is a full zk-proof generation. Repeated requests can consume disproportionate CPU on the node, degrading availability of the shielded-API-enabled node (and potentially other services sharing the same process/threads) — a low-cost denial-of-service against nodes that expose `allowShieldedTransactionApi=true`.

### Likelihood Explanation
Preconditions are exactly those in the prompt: `allowShieldedTransactionApi=true` (a documented public-facing feature flag, not privileged), and any valid-format `ak`/`nsk`/`ovk` (the attacker can generate their own spending key locally — nothing tied to real funds is required). Building a syntactically valid but network-unknown Merkle voucher/anchor is straightforward using the same `IncrementalMerkleTreeContainer`/`IncrementalMerkleVoucherContainer` APIs available in the public SDK/test code (e.g., as done in `createSimpleMerkleVoucherContainer` in the test suite) — no special access is needed. The only mitigation present is generic per-endpoint/IP QPS rate limiting in `RateLimiterServlet`, which throttles request rate but does not address the underlying cost asymmetry per request; an attacker can still send proof-generation requests at the servlet's allowed rate indefinitely to consume CPU.

### Recommendation
Before invoking `generateSpendProof`/`buildWithoutAsk` in `Wallet.createShieldedTransactionWithoutSpendAuthSig`, validate that each `spendNote.getVoucher().getRt()` (anchor) exists in the node's `MerkleContainer` (`chainBaseManager.getMerkleContainer().merkleRootExist(rt)`), and throw a `ContractValidateException`/`ZksnarkException` immediately if it does not, mirroring the check already performed in `ShieldedTransferActuator.validate()`. This moves the cheap check ahead of the expensive zk-proof generation, closing the cost asymmetry. Additionally, consider applying a stricter, proof-generation-specific rate limit/cost accounting for this and related shielded-proof-building endpoints (`CreateShieldedTransactionServlet`, `CreateShieldedContractParametersServlet`, etc.) that are similarly reachable and susceptible to the same pattern.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/zksnark/ShieldedAnchorDosTest.java
@Test
public void testCreateShieldedTransactionWithoutSpendAuthSig_UnknownAnchorStillProves() throws Exception {
  Args.getInstance().setAllowShieldedTransactionApi(true);

  // Attacker builds a locally fabricated note + voucher/anchor, NOT stored in MerkleContainer
  SpendingKey sk = SpendingKey.random();
  ExpandedSpendingKey expsk = sk.expandedSpendingKey();
  PaymentAddress address = sk.defaultAddress();
  Note note = new Note(address, 100 * 1000000L);
  IncrementalMerkleTreeContainer tree =
      new IncrementalMerkleTreeContainer(new IncrementalMerkleTreeCapsule());
  PedersenHashCapsule cmCapsule = new PedersenHashCapsule();
  cmCapsule.setContent(ByteString.copyFrom(note.cm()));
  tree.append(cmCapsule.getInstance());
  IncrementalMerkleVoucherContainer voucher = tree.toVoucher();
  byte[] fakeAnchor = voucher.root().getContent().toByteArray();

  // Deliberately DO NOT call merkleContainer.putMerkleTreeIntoStore(fakeAnchor, ...)
  // so this anchor is unknown to the node.
  Assert.assertFalse(wallet.getChainBaseManager()
      .getMerkleContainer().merkleRootExist(fakeAnchor));

  PrivateParametersWithoutAsk.Builder req = PrivateParametersWithoutAsk.newBuilder();
  req.setAk(ByteString.copyFrom(sk.fullViewingKey().getAk()));
  req.setNsk(ByteString.copyFrom(expsk.getNsk()));
  req.setOvk(ByteString.copyFrom(expsk.getOvk()));
  // populate SpendNote with note/voucher/anchor built above, and a shielded receive note

  long start = System.nanoTime();
  TransactionCapsule trx =
      wallet.createShieldedTransactionWithoutSpendAuthSig(req.build());
  long elapsedMs = (System.nanoTime() - start) / 1_000_000;

  // Expected today (vulnerable): proof generation SUCCEEDS despite unknown anchor,
  // and elapsedMs reflects a full Groth16 proof (order of 10s-100s of ms),
  // demonstrating expensive work performed with zero validation of chain state.
  Assert.assertNotNull(trx);
  System.out.println("Proof generation time (ms) for unknown anchor: " + elapsedMs);

  // Only NOW, at broadcast/validate time, does the anchor get rejected:
  List<Actuator> actuators = ActuatorCreator.getINSTANCE().createActuator(trx);
  try {
    actuators.get(0).validate();
    Assert.fail("expected Rt is invalid.");
  } catch (ContractValidateException e) {
    Assert.assertEquals("Rt is invalid.", e.getMessage());
  }
}
```
Expected assertion for the fix: after adding an anchor pre-check in `Wallet.createShieldedTransactionWithoutSpendAuthSig`, this test should instead observe `createShieldedTransactionWithoutSpendAuthSig` throwing immediately (before any `librustzcashSaplingSpendProof` call) with a cheap validation error, and the measured CPU/time cost for repeated invalid-anchor requests should be bounded and negligible compared to the current proof-generation cost.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/CreateShieldedTransactionWithoutSpendAuthSigServlet.java (L26-34)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      PrivateParametersWithoutAsk.Builder build = PrivateParametersWithoutAsk.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      Transaction tx = wallet
          .createShieldedTransactionWithoutSpendAuthSig(build.build())
          .getInstance();
      String txString = Util.printCreateTransaction(tx, params.isVisible());
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2438-2460)
```java
      if (!(ArrayUtils.isEmpty(ak) || ArrayUtils.isEmpty(nsk) || ArrayUtils.isEmpty(ovk))) {
        for (SpendNote spendNote : shieldedSpends) {
          GrpcAPI.Note note = spendNote.getNote();
          PaymentAddress paymentAddress = KeyIo.decodePaymentAddress(
              note.getPaymentAddress());
          if (paymentAddress == null) {
            throw new ZksnarkException(PAYMENT_ADDRESS_FORMAT_WRONG);
          }
          Note baseNote = new Note(paymentAddress.getD(),
              paymentAddress.getPkD(), note.getValue(), note.getRcm().toByteArray());

          IncrementalMerkleVoucherContainer voucherContainer =
              new IncrementalMerkleVoucherCapsule(
                  spendNote.getVoucher()).toMerkleVoucherContainer();
          builder.addSpend(ak,
              nsk,
              ovk,
              baseNote,
              spendNote.getAlpha().toByteArray(),
              spendNote.getVoucher().getRt().toByteArray(),
              voucherContainer);
        }
      }
```

**File:** framework/src/main/java/org/tron/core/zen/ZenTransactionBuilder.java (L210-254)
```java
  // Note: should call librustzcashSaplingProvingCtxFree in the caller
  public SpendDescriptionCapsule generateSpendProof(SpendDescriptionInfo spend,
      long ctx) throws ZksnarkException {

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

    byte[] cv = new byte[32];
    byte[] rk = new byte[32];
    byte[] zkproof = new byte[192];
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

**File:** actuator/src/main/java/org/tron/core/actuator/ShieldedTransferActuator.java (L229-244)
```java
    List<SpendDescription> spendDescriptions = shieldedTransferContract.getSpendDescriptionList();
    // check duplicate sapling nullifiers
    if (CollectionUtils.isNotEmpty(spendDescriptions)) {
      HashSet<ByteString> nfSet = new HashSet<>();
      for (SpendDescription spendDescription : spendDescriptions) {
        if (nfSet.contains(spendDescription.getNullifier())) {
          throw new ContractValidateException("duplicate sapling nullifiers in this transaction");
        }
        nfSet.add(spendDescription.getNullifier());
        if (!merkleContainer.merkleRootExist(spendDescription.getAnchor().toByteArray())) {
          throw new ContractValidateException("Rt is invalid.");
        }
        if (nullifierStore.has(spendDescription.getNullifier().toByteArray())) {
          throw new ContractValidateException("note has been spend in this transaction");
        }
      }
```

**File:** framework/src/test/java/org/tron/core/zksnark/SendCoinShieldTest.java (L1777-1812)
```java
  @Test
  public void TestWrongAnchor() throws Exception {
    dbManager.getDynamicPropertiesStore().saveAllowShieldedTransaction(1);
    {
      ZenTransactionBuilder builder = new ZenTransactionBuilder(wallet) {
        //set wrong anchor
        @Override
        public SpendDescriptionCapsule generateSpendProof(SpendDescriptionInfo spend, long ctx)
            throws ZksnarkException {
          SpendDescriptionCapsule spendDescriptionCapsule = super.generateSpendProof(spend, ctx);
          //The format is correct, but it does not belong to this
          // note value ,fake : 200_000_000,real:20_000_000
          byte[] bytes = ByteArray.fromHexString(
              "bd7e296f492ffc23248b1815277b29af3a8970fff70f8256492bbea79b9a5e3e");//256
          System.out.println(
              "bytes:" + ByteArray.toHexString(spendDescriptionCapsule.getAnchor().toByteArray()));
          spendDescriptionCapsule.setAnchor(bytes);
          spendDescriptionCapsule.setAnchor(ByteString.copyFrom(bytes));
          spendDescriptionCapsule.setValueCommitment(new byte[32]);
          spendDescriptionCapsule.setValueCommitment(ByteString.copyFrom(new byte[32]));
          return spendDescriptionCapsule;
        }
      };

      TransactionCapsule transactionCapsule = generateDefaultBuilder(builder);

      try {
        executeTx(transactionCapsule);
        Assert.fail();
      } catch (ContractValidateException e) {
        if (!e.getMessage().equals("Rt is invalid.")) {
          throw e;
        }
        System.out.println("Done");
      }
    }
```
