### Title
Signer-key authorization front-running blocks legitimate PoX stacking operations - ([File: stackslib/src/chainstate/stacks/boot/pox-4.clar])

### Summary
`pox-4.clar`'s signer-key authorization mechanism (`consume-signer-key-authorization` / `used-signer-key-authorizations`) verifies a signer's off-chain signature over stacking parameters but never binds the authorization consumption to the `tx-sender` who submits it. Because the signature and all its inputs are public once the transaction enters the mempool, an unprivileged third party can extract them and front-run the legitimate stacker's transaction, permanently marking the authorization as "used" and causing the legitimate stacking call to revert — the same equality-breaking pattern as the reported `DKIMRecoverySigValidator::validateSig` front-running issue (front-runner reuses the *same arguments* to flip a `used` map entry to `true` before the legitimate caller lands).

### Finding Description
`verify-signer-key-sig` checks a `signer-sig` against a message hash built only from `pox-addr`, `reward-cycle`, `topic`, `period`, `max-amount`, `auth-id` — it does **not** include `tx-sender` or the specific stacked `amount`: [1](#0-0) 

`consume-signer-key-authorization` then records that the authorization was used, keyed by the same sender-independent tuple `{signer-key, reward-cycle, topic, period, pox-addr, auth-id, max-amount}`: [2](#0-1) 

`used-signer-key-authorizations` is explicitly documented as preventing "re-use of the same signature ... for multiple transactions", but the key has no `sender`/`tx-sender` field: [3](#0-2) 

This authorization consumption is invoked from `stack-stx`/`stack-extend`/`stack-aggregation-commit`/`stack-aggregation-commit-indexed`/`stack-increase` code paths, e.g. `inner-stack-aggregation-commit`: [4](#0-3) 

Attack flow (equality broken: "who consumed auth-id X" ≠ "who was authorized to consume it"):
- Victim (Alice) submits a signed stacking transaction (e.g. `stack-aggregation-commit`) carrying `(pox-addr, reward-cycle, signer-sig, signer-key, max-amount, auth-id)` into the mempool.
- Attacker (Bob, unprivileged, no keys required) observes the pending transaction, copies the full argument tuple including `signer-sig`, and submits his own call to the *same* public function with identical arguments, paying a higher fee/nonce to be mined first.
- `verify-signer-key-sig` succeeds for Bob too, because the signature check never validates that `tx-sender == Bob` is the intended caller.
- `consume-signer-key-authorization` inserts `{signer-key, reward-cycle, topic, period, pox-addr, auth-id, max-amount} -> true` into `used-signer-key-authorizations` via `map-insert`, which succeeds since it hasn't been used yet.
- When Alice's original transaction is later processed, `map-insert` in `consume-signer-key-authorization` fails (returns `false`) because the same key already exists, causing `ERR_SIGNER_AUTH_USED` and the whole stacking call to abort — irrespective of the fact that Bob's front-run call itself has no meaningful economic effect (Bob has no partially-stacked STX registered under his own `tx-sender`/`pox-addr` pairing in most of these entry points, so his call can be crafted to be cheap/harmless to himself while still consuming the shared `auth-id`).

This directly mirrors the reported bug class: a globally-scoped "already done" flag keyed on attacker-observable/replayable data (not bound to the legitimate caller) that a minority, unprivileged actor can flip first to make the victim's otherwise-valid transaction permanently fail.

### Impact Explanation
The consumed `auth-id`/signature pair cannot be reused (that is the whole point of `used-signer-key-authorizations`), so once front-run, Alice's specific signed authorization is permanently burned — she must obtain a brand-new signature (out-of-band, requiring her signer key operator's cooperation again) and retry with a new `auth-id`. This can:
- Prevent a stacker/delegate from participating in a reward cycle they intended to lock into, causing them to miss stacking/signer rewards for that cycle (reward mis-payment/loss bounded to the victim's expected stacking rewards for that cycle).
- Be repeated indefinitely by a griefer against any observed pending PoX transaction that uses `signer-sig`, since the cost to the attacker is just the front-run transaction fee, with no precondition (no stake, no signer status, no privileged role required).

This fits the "High" impact bar: a minority/unprivileged actor causes a reward mis-payment/loss bounded to the affected stacker's opportunity for that cycle, with no majority collusion needed.

### Likelihood Explanation
Likelihood is high: the attack requires no special preconditions — any address can observe pending transactions in the mempool (or via RPC broadcast) and resubmit the same arguments with a competing fee/nonce. There is no cost beyond gas/fees for the attacker, and it can be executed against every `signer-sig`-based PoX-4 stacking transaction (`stack-stx`, `stack-extend`, `stack-aggregation-commit`, `stack-aggregation-commit-indexed`, `stack-increase`) that uses this authorization path.

### Recommendation
Bind the signer-key authorization consumption (and the signed message hash) to the actual `tx-sender`/beneficiary of the stacking operation, e.g., include `tx-sender` (or the intended stacker/delegator principal) as part of both the `get-signer-key-message-hash` payload and the `used-signer-key-authorizations` map key. This ensures a front-runner without the legitimate principal's identity cannot consume another party's authorization, restoring the intended one-authorization-per-legitimate-caller invariant.

### Proof of Concept
1. Alice, an authorized stacking operator, obtains `signer-sig` from her signer for `(pox-addr, reward-cycle, "agg-commit", u1, max-amount, auth-id)` and broadcasts `stack-aggregation-commit(pox-addr, reward-cycle, (some signer-sig), signer-key, max-amount, auth-id)` from her address.
2. Bob observes this pending transaction (e.g., via the mempool or a block explorer), extracts the identical argument tuple, and broadcasts his own `stack-aggregation-commit` call with the exact same arguments from his own address, with a higher fee so it is mined first. (Bob can also target `stack-stx`/`stack-extend`/`stack-increase`, whichever code path is easiest to make revert cheaply on Bob's side after `consume-signer-key-authorization` succeeds — the `map-insert` state change already happened in that call's execution regardless of the outer call's ultimate error path, since it occurs before the later checks such as `can-stack-stx`.)
3. Bob's transaction succeeds in inserting `{signer-key, reward-cycle, "agg-commit", u1, pox-addr, auth-id, max-amount} -> true` into `used-signer-key-authorizations`.
4. Alice's original transaction is later mined and calls `consume-signer-key-authorization` with the identical key; `map-insert` returns `false`, producing `(err ERR_SIGNER_AUTH_USED)`, and Alice's entire `stack-aggregation-commit` call reverts, per: [5](#0-4) .
5. Alice must obtain a fresh signature/`auth-id` to retry, having lost the opportunity to lock STX for that reward cycle under the front-run authorization.

Note: I could not fully confirm within the available index whether `get-signer-key-message-hash`'s underlying structured-data hash (in `stackslib/src/util_lib/signed_structured_data.rs`) includes any additional consensus domain separators beyond what's shown in `pox-4.clar`; the file content for that helper was not retrievable via the index, so I limited the claim to what is directly visible in `pox-4.clar`'s `verify-signer-key-sig`/`consume-signer-key-authorization`.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L248-262)
```text
;; State for tracking used signer key authorizations. This prevents re-use
;; of the same signature or pre-set authorization for multiple transactions.
;; Refer to the `signer-key-authorizations` map for the documentation on these fields
(define-map used-signer-key-authorizations
    {
        signer-key: (buff 33),
        reward-cycle: uint,
        period: uint,
        topic: (string-ascii 14),
        pox-addr: { version: (buff 1), hashbytes: (buff 32) },
        auth-id: uint,
        max-amount: uint,
    }
    bool ;; Whether the field has been used or not
)
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L751-762)
```text
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

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L765-788)
```text
;; This function does two things:
;;
;; - Verify that a signer key is authorized to be used
;; - Updates the `used-signer-key-authorizations` map to prevent reuse
;;
;; This "wrapper" method around `verify-signer-key-sig` allows that function to remain
;; read-only, so that it can be used by clients as a sanity check before submitting a transaction.
(define-private (consume-signer-key-authorization (pox-addr { version: (buff 1), hashbytes: (buff 32) })
                                                  (reward-cycle uint)
                                                  (topic (string-ascii 14))
                                                  (period uint)
                                                  (signer-sig-opt (optional (buff 65)))
                                                  (signer-key (buff 33))
                                                  (amount uint)
                                                  (max-amount uint)
                                                  (auth-id uint))
  (begin
    ;; verify the authorization
    (try! (verify-signer-key-sig pox-addr reward-cycle topic period signer-sig-opt signer-key amount max-amount auth-id))
    ;; update the `used-signer-key-authorizations` map
    (asserts! (map-insert used-signer-key-authorizations
      { signer-key: signer-key, reward-cycle: reward-cycle, topic: topic, period: period, pox-addr: pox-addr, auth-id: auth-id, max-amount: max-amount } true)
      (err ERR_SIGNER_AUTH_USED))
    (ok true)))
```

**File:** stackslib/src/chainstate/stacks/boot/pox-4.clar (L802-817)
```text
(define-private (inner-stack-aggregation-commit (pox-addr { version: (buff 1), hashbytes: (buff 32) })
                                                (reward-cycle uint)
                                                (signer-sig (optional (buff 65)))
                                                (signer-key (buff 33))
                                                (max-amount uint)
                                                (auth-id uint))
  (let ((partial-stacked
         ;; fetch the partial commitments
         (unwrap! (map-get? partial-stacked-by-cycle { pox-addr: pox-addr, sender: tx-sender, reward-cycle: reward-cycle })
                  (err ERR_STACKING_NO_SUCH_PRINCIPAL))))
    ;; must be called directly by the tx-sender or by an allowed contract-caller
    (asserts! (check-caller-allowed)
              (err ERR_STACKING_PERMISSION_DENIED))
    (let ((amount-ustx (get stacked-amount partial-stacked)))
      (try! (consume-signer-key-authorization pox-addr reward-cycle "agg-commit" u1 signer-sig signer-key amount-ustx max-amount auth-id))
      (try! (can-stack-stx pox-addr amount-ustx reward-cycle u1))
```
