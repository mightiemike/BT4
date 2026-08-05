Confirmed analog: `RelayService.checkHelloMessage()` verifies a witness's signature over `msg.getTimestamp()` to grant trust-node status, but never checks that timestamp against a freshness window (no min/max bound at all — strictly worse than the original bug, which at least checked an upper bound).

### Title
Missing timestamp freshness check in fast-forward `HelloMessage` signature verification allows replay of stale witness signatures - (File: framework/src/main/java/org/tron/core/net/service/relay/RelayService.java)

### Summary
`RelayService.checkHelloMessage()` verifies a signature produced by `fillHelloMessage()` over `msg.getTimestamp()` [1](#0-0) , and if the recovered address matches an active witness, adds the connecting peer's IP to the trusted P2P node list [2](#0-1) . Unlike `setSymbolsPrice()` which at least bounds `priceSig.timestamp` from above, this code performs **no timestamp bound check whatsoever** — the signed `timestamp` value is only used as the message being signed, never compared against wall-clock time or block time.

### Finding Description
The signature covers only `Sha256Hash.of(ByteArray.fromLong(msg.getTimestamp()))` [3](#0-2) . There is no field or check anywhere in `checkHelloMessage` that constrains `msg.getTimestamp()` to be recent (e.g., within some `Args.getInstance().getMaxHelloTimeDiff()` of `System.currentTimeMillis()`), nor is there a replay-cache preventing reuse of a previously observed `(address, timestamp, signature)` tuple. `HandshakeService.processHelloMessage()`, the caller, likewise performs no timestamp-freshness validation before invoking `relayService.checkHelloMessage(msg, peer.getChannel())` [4](#0-3) . Consequently, any attacker who ever observes a single valid `(HelloMessage.getAddress(), timestamp, signature)` from a live active witness (e.g., via passive network capture, since P2P handshakes are unencrypted) can indefinitely replay that exact tuple from any IP to any fast-forward-enabled node and be added to `TronNetService.getP2pConfig().getTrustNodes()`.

This mirrors the root cause of the reported bug class: a signed payload that binds only to a `timestamp` field is accepted without any freshness/staleness validation on that timestamp, enabling arbitrary-age replay of the signed artifact.

### Impact Explanation
Being added to `getTrustNodes()` grants elevated trust-level P2P handling for that IP within the fast-forward/relay subsystem, which is designed specifically to fast-track/trust block propagation from real active witnesses. An attacker replaying a captured witness handshake signature can impersonate a trusted witness peer perpetually (as long as that witness remains in the active set) without ever possessing the witness's private key, undermining the trust-node mechanism's integrity guarantee. This only affects nodes running with `isFastForward()` enabled, and note that the underlying block content is still independently validated elsewhere in the consensus pipeline (e.g., `validateSignature`, `processTransaction`) — so this does not directly forge blocks or transactions, but it does defeat the intended authentication purpose of the relay trust-node feature, an accounting/trust boundary the code explicitly tries to enforce.

### Likelihood Explanation
Exploitation requires: (1) the target node to run with `fastForward` enabled (an opt-in feature, not default), and (2) the attacker to have captured one genuine `HelloMessage` signature broadcast by an active witness on the P2P network at some point (unencrypted P2P handshake). Given that witnesses periodically reconnect to configured fast-forward peers under `RelayService.connect()`, valid signed hello messages are broadcast repeatedly, making capture feasible for anyone observing network traffic to/from a witness's fast-forward peer connections. No privileged keys or roles are needed by the attacker themselves.

### Recommendation
Bind and check `msg.getTimestamp()` against `System.currentTimeMillis()` with a bounded window (e.g., reject if `Math.abs(now - msg.getTimestamp()) > MAX_HELLO_TIME_DIFF`) inside `checkHelloMessage()`, and consider caching recently-seen `(address, signature)` pairs to reject exact replays even within the freshness window.

### Proof of Concept
1. Enable `fastForward` on a target node and configure a `fastForwardNodes` entry pointing at (or reachable through) a real active witness `W`.
2. Passively capture the P2P handshake traffic between `W` and any of its configured `fastForwardNodes` peers, extracting one valid `Protocol.HelloMessage` containing `W`'s address, an old `timestamp`, and `signature` (as produced by `fillHelloMessage` at [1](#0-0) ).
3. From an attacker-controlled node/IP, initiate a handshake with the target node and send the identical captured `HelloMessage` (same address, timestamp, signature) — reusing it any time later, even long after `W`'s witness term or long after the message was originally sent.
4. `HandshakeService.processHelloMessage` calls `relayService.checkHelloMessage(msg, peer.getChannel())` [4](#0-3) ; because `W` is still in `witnessScheduleStore.getActiveWitnesses()` and the signature recovers to `W`'s address, `flag` is `true` and the attacker's IP is added to `TronNetService.getP2pConfig().getTrustNodes()` [2](#0-1)  — despite the attacker never holding `W`'s private key and the signed message being arbitrarily stale.

### Citations

**File:** framework/src/main/java/org/tron/core/net/service/relay/RelayService.java (L107-124)
```java
  public void fillHelloMessage(HelloMessage message, Channel channel) {
    if (isActiveWitness()) {
      fastForwardNodes.forEach(address -> {
        if (address.getAddress().equals(channel.getInetAddress())) {
          SignInterface cryptoEngine = SignUtils
              .fromPrivate(ByteArray.fromHexString(Args.getLocalWitnesses().getPrivateKey()),
                  Args.getInstance().isECKeyCryptoEngine());

          ByteString sig = ByteString.copyFrom(cryptoEngine.Base64toBytes(cryptoEngine
              .signHash(Sha256Hash.of(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), ByteArray.fromLong(message
                  .getTimestamp())).getBytes())));
          message.setHelloMessage(message.getHelloMessage().toBuilder()
              .setAddress(witnessAddress).setSignature(sig).build());
        }
      });
    }
  }
```

**File:** framework/src/main/java/org/tron/core/net/service/relay/RelayService.java (L159-179)
```java
    boolean flag;
    try {
      Sha256Hash hash = Sha256Hash.of(CommonParameter
          .getInstance().isECKeyCryptoEngine(), ByteArray.fromLong(msg.getTimestamp()));
      String sig =
          TransactionCapsule.getBase64FromByteString(msg.getSignature());
      byte[] sigAddress = SignUtils.signatureToAddress(hash.getBytes(), sig,
          Args.getInstance().isECKeyCryptoEngine());
      if (manager.getDynamicPropertiesStore().getAllowMultiSign() != 1) {
        flag = Arrays.equals(sigAddress, msg.getAddress().toByteArray());
      } else {
        byte[] witnessPermissionAddress = manager.getAccountStore()
            .get(msg.getAddress().toByteArray()).getWitnessPermissionAddress();
        flag = Arrays.equals(sigAddress, witnessPermissionAddress);
      }
      if (flag) {
        TronNetService.getP2pConfig().getTrustNodes().add(channel.getInetAddress());
        DesensitizedConverter.addSensitive(channel.getInetAddress().toString().substring(1),
            ByteArray.toHexString(msg.getAddress().toByteArray()));
      }
      return flag;
```

**File:** framework/src/main/java/org/tron/core/net/service/handshake/HandshakeService.java (L67-70)
```java
    if (!relayService.checkHelloMessage(msg, peer.getChannel())) {
      peer.disconnect(ReasonCode.UNEXPECTED_IDENTITY);
      return;
    }
```
