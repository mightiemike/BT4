## Analog Vulnerability: Front-Running Signature Consumption in `verify-signer-key-sig` / `consume-signer-key-authorization` DoS's Legitimate Stackers

### Title
Signer-key authorization front-running lets an unprivileged party burn a stacker's signature before it lands, causing bounded reward loss - (File: `stackslib/src/chainstate/stacks/boot/pox-4.clar`)

### Summary
The external report describes a classic "claim the unique key first" front-running DoS: a legitimate `createAccount(accountId, ...)` call is preempted by an attacker submitting a transaction with the identical `accountId`, permanently consuming that key and reverting the legitimate caller. The same structural pattern - a public/mempool-visible unique tuple that any unprivileged party can insert first to burn it - exists in the PoX-4 signer-authorization replay-protection scheme.

### Finding Description
`verify-signer-key-sig`/`consume-signer-key-authorization` gate stacking calls (`stack-stx`, `stack-extend`, `stack-aggregation-commit`, `stack-aggregation-increase`) on a signature from the signer key over a message containing `{pox-addr, reward-cycle, topic, period, auth-id, max-amount}`. Replay protection is enforced purely by inserting this tuple into `used-signer-key-authorizations`: [1](#0-0) [2](#0-1) 

The signature itself (`signer-sig`), and every other field needed to replay it, are plaintext arguments to a public function call. Once a legitimate stacker broadcasts, say, `stack-aggregation-commit(pox-addr, reward-cycle, signer-sig, signer-key, max-amount, auth-id)`, anyone observing the mempool can read this signature and all its parameters. Nothing in `verify-signer-key-sig` binds the consumption to `tx-sender` being the original signer or intended stacker - the check is only `secp256k1-recover?` matches `signer-key`, which any caller can trivially satisfy by replaying the exact same signature/tuple: [3](#0-2) 

An attacker can front-run with their own transaction using the identical `(pox-addr, reward-cycle, topic, period, auth-id, max-amount, signer-key, signer-sig)` tuple through any of the calling functions that ultimately reach `consume-signer-key-authorization` (e.g. `inner-stack-aggregation-commit`, `stack-aggregation-increase`). Because `map-insert` on `used-signer-key-authorizations` succeeds only once, the attacker's transaction - if it lands first - marks the tuple used, and the legitimate stacker's original transaction then hits `ERR_SIGNER_AUTH_USED` (err 19) and reverts: [4](#0-3) 

This exactly mirrors the reported bug class: a unique, publicly-visible identifier (`accountId` in the report; here the `(signer-key, reward-cycle, period, topic, pox-addr, auth-id, max-amount)` tuple) that is claimed on a strict first-come basis, with the claiming transaction requiring nothing the legitimate party doesn't already reveal on-chain/in-mempool.

### Impact Explanation
This is a minority-triggerable (single unprivileged attacker, no majority/collusion needed), unprivileged-actor griefing vector bounded to a fee/opportunity loss rather than a chain split: a stacker whose stacking authorization is front-run and consumed must generate a brand-new signature with a fresh `auth-id` and resubmit. Because PoX stacking transactions must confirm within specific reward-cycle/prepare-phase windows (see the `reward-cycle` and window checks throughout `pox-4.clar`), a repeatedly front-run stacker can be forced to miss the confirmation window for that cycle entirely, causing them to lose participation (and thus rewards) for that cycle - a reward loss bounded to the victim's own missed cycle, not a protocol-wide double-payment or state-root divergence. This aligns with the "High" impact tier (poison/reward mis-payment bounded to fees, temporary disagreement) rather than "Critical," since no consensus equality (sortition winner, state root, block validity) is broken - only an intra-contract replay-protection guarantee that "only the intended holder of this authorization gets to consume it."

### Likelihood Explanation
Likelihood is limited by the requirement that the attacker be able to satisfy the surrounding preconditions of whichever calling function they use (e.g., for `stack-aggregation-commit` they need their own pre-existing `partial-stacked-by-cycle` entry for that `(pox-addr, sender, reward-cycle)`; for `stack-stx`/`stack-extend`-style paths they would need to lock a stackable amount of their own STX). This is unlike the original report where front-running was literally free - here the attacker generally must commit some of their own resources, and I was not able to fully trace every consuming call path (`stack-stx`/`stack-extend` bodies) within the tool budget to confirm whether a near-zero-cost path exists to trigger `consume-signer-key-authorization` before the surrounding balance/threshold checks. This is a material gap: the practical cost and thus the true likelihood depends on that unverified ordering.

### Recommendation
Bind the authorization consumption to the intended caller, e.g., by including `tx-sender` (or an explicit designated recipient principal) in the SIP-018 signed message and/or the `used-signer-key-authorizations` key, so that only the party the signer actually authorized can consume the signature. Alternatively/additionally, allow the legitimate signer to pre-register an authorization (already possible via `signer-key-authorizations` + `set-signer-key-authorization`) restricted to a specific principal, and prefer that path over ad-hoc raw-signature replay for any flow where mempool front-running is a concern.

### Proof of Concept
Conceptual sequence (not independently executed against a live node in this session):
1. Alice (legitimate stacker) signs a PoX-4 signer-key authorization for `(pox-addr, reward-cycle, "agg-commit", period=1, auth-id=N, max-amount=M)` and broadcasts `stack-aggregation-commit(pox-addr, reward-cycle, some(sig), signer-key, M, N)`.
2. Mallory observes this pending transaction in the mempool, extracting the fully public `(pox-addr, reward-cycle, sig, signer-key, M, N)` tuple.
3. Mallory, having previously called `stack-aggregation-commit`/partial-stack setup herself for the same `pox-addr`/`reward-cycle` (or via whichever consuming entrypoint requires the least setup), submits her own transaction reusing the identical tuple with higher fee/priority.
4. Mallory's transaction confirms first, `map-insert` into `used-signer-key-authorizations` succeeds, marking the tuple used - see [5](#0-4) .
5. Alice's original transaction now fails `verify-signer-key-sig`'s `is-none` check and reverts with `ERR_SIGNER_AUTH_USED` - see [4](#0-3) .
6. Alice must obtain a fresh signature/`auth-id` and resubmit, potentially missing the reward-cycle stacking window.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L744-762)
```text
  (begin
    ;; Validate that amount is less than or equal to `max-amount`
    (asserts! (>= max-amount amount) (err ERR_SIGNER_AUTH_AMOUNT_TOO_HIGH))
    (asserts! (is-none (map-get? used-signer-key-authorizations { signer-key: signer-key, reward-cycle: reward-cycle, topic: topic, period: period, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount }))
              (err ERR_SIGNER_AUTH_USED))
    (match signer-sig-opt
      ;; `signer-sig` is present, verify the signature
      signer-sig (ok (asserts!
        (is-eq
          (unwrap! (secp256k1-recover?
            (get-signer-key-message-hash pox-addr reward-cycle topic period max-amount auth-id)
            signer-sig) (err ERR_INVALID_SIGNATURE_RECOVER))
          signer-key)
        (err ERR_INVALID_SIGNATURE_PUBKEY)))
      ;; `signer-sig` is not present, verify that an authorization was previously added for this key
      (ok (asserts! (default-to false (map-get? signer-key-authorizations
            { signer-key: signer-key, reward-cycle: reward-cycle, period: period, topic: topic, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount }))
          (err ERR_NOT_ALLOWED)))
    ))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L781-788)
```text
  (begin
    ;; verify the authorization
    (try! (verify-signer-key-sig pox-addr reward-cycle topic period signer-sig-opt signer-key amount max-amount auth-id))
    ;; update the `used-signer-key-authorizations` map
    (asserts! (map-insert used-signer-key-authorizations
      { signer-key: signer-key, reward-cycle: reward-cycle, topic: topic, period: period, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount } true)
      (err ERR_SIGNER_AUTH_USED))
    (ok true)))
```
