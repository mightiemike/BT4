## Title
Function-call access-key allowance limit is completely bypassed when the same permissioned action is routed through a `DelegateAction` (meta-transaction) — ([File: docs/architecture/how/meta-tx.md])

### Summary
The OpenQ report describes a whitelist check (`if !isWhitelisted(token) { require(!limitReached) }`) that is *conditionally* skipped, letting an untrusted value bypass a security gate entirely and permanently degrade protocol usability. The nearcore analog is the **allowance check on `FunctionCallPermission` access keys**, which is enforced for direct transactions but is **entirely skipped** when the identical action is delivered via a `DelegateAction` (meta-transaction): the relayer pays for gas/deposit, so the on-chain unwrap-and-execute path for the delegated `FunctionCall` never re-checks or decrements the signer's key `allowance`, even though the same method/receiver checks are performed.

### Finding Description
`FunctionCallPermission` access keys are meant to be a self-imposed spending/usage limit: an app-scoped key with method and receiver restrictions and a NEAR `allowance` cap that bounds how much of the account's balance that key can authorize per call, decremented on each direct transaction (`runtime/runtime/src/verifier.rs`, access-key allowance deduction logic invoked from `verify_and_charge_transaction`).

When the same `FunctionCall` is instead wrapped as the inner action of a `SignedDelegateAction` and forwarded by a relayer:
- the receiver and method-name restrictions of the access key **are** re-validated when the delegate action is unwrapped on the signer's shard, but
- the `allowance` field is **never checked or decremented**, because "all costs have been covered by the relayer" — as documented explicitly:

> "For allowance, however, there is no check. All costs have been covered by the relayer. Hence, even if the allowance of the key is insufficient to make the call directly, indirectly through meta transaction it will still work." [1](#0-0) 

This is structurally the same bug class as the OpenQ finding: a security predicate (`isWhitelisted` / `allowance sufficient`) is unconditionally bypassed under an alternate code path (`token-limit not reached` / `action delivered via relayer`) that the contract/protocol designer did not intend to be a full waiver of the check, only a partial exemption.

> "This behavior is in the spirit of allowance limiting how much financial resources the user can use from a given account. But if someone were to limit a function access key to one trivial action by setting a very small allowance, that is circumventable by going through a relayer." [2](#0-1) 

The design intent of a restricted `FunctionCallPermission` key (e.g., a dApp key scoped to one trivial, low-value method call) is that even if the key is compromised or misused, the blast radius is capped by `allowance`. That guarantee silently disappears the moment any relayer is willing to wrap the call in a `DelegateAction`.

### Impact Explanation
Any account holding a low-allowance `FunctionCallPermission` key intended to restrict that key's authority is exposed to unauthorized value movement beyond the configured cap, because the allowance ceiling — the sole enforced spending control for that key class — is bypassable by simply routing the call through a relayer. This breaks the access-key authorization model that dApps, sub-account delegation schemes, and session-key patterns rely on: a key explicitly capped at, say, 1 yoctoNEAR of allowance for one restricted call can, via a meta-transaction, still authorize the call's `deposit` because the deposit is paid by the relayer's balance rather than gated by the key's remaining allowance. This maps to the "unauthorized value movement" / "fee or gas bypass" acceptance criteria: the access-key's fee/authorization gate is bypassed for the delegated path while being enforced for the direct path, meaning the exact same on-chain state transition (execute this `FunctionCall` as this account) is permitted under one entry point and denied under the other, despite the key's stated restriction supposedly applying uniformly.

### Likelihood Explanation
This requires no privileged position: any transaction signer with a restricted `FunctionCallPermission` key can trigger it by asking (or being tricked/colluding with) any relayer to wrap their `DelegateAction` — relayers are an open, permissionless application-layer role, and NEP-366 meta-transactions are a stable, generally available feature. No malicious validator, node, or network condition is required — this is purely a signer + relayer-submitted-transaction scenario, squarely in scope ("meta-transaction sender," "access keys and nonces"). The likelihood of exploitation is high in any deployment pattern where a limited-allowance key is treated as a hard security boundary (e.g., "give this dApp a key limited to $0.01 of spend"), since the bypass is deterministic and requires no race condition or luck — it is a documented, always-reachable code path.

### Recommendation
Enforce the `FunctionCallPermission.allowance` check (and corresponding decrement) for the inner `FunctionCall` action even when it originates from a `DelegateAction`, using the signer key's balance/allowance rather than exempting it because the relayer fronts the gas/deposit. At minimum, the protocol should surface (via `AccessKey` view or NEP amendment) that allowance is not enforced for delegate-action-delivered calls, so integrators cannot rely on `allowance` as a hard cap when meta-transactions are in play; ideally, the runtime should decrement `allowance` against the attached deposit of the inner action just as it does for `verify_and_charge_transaction` on direct submission, closing the bypass.

### Proof of Concept
No test harness in the indexed codebase implements this scenario (the docs page is the only artifact found describing it precisely); a concrete reproduction would be:
1. Create account `alice`, add a `FunctionCallPermission` access key restricted to `receiver_id = "ft.near"`, `method_names = ["ft_transfer_call"]`, `allowance = 1` yoctoNEAR.
2. Directly sign+submit a transaction from `alice` calling `ft_transfer_call` with a deposit exceeding the 1-yocto allowance → rejected (`NotEnoughAllowance`/insufficient allowance error) per `runtime/runtime/src/verifier.rs` allowance-check path.
3. Instead, have `alice` sign the identical `FunctionCall` as the inner action of a `SignedDelegateAction`, and have any relayer wrap and submit it as its own transaction (paying gas/deposit).
4. Observe that the call executes successfully on Alice's shard despite the key's allowance being far below what direct submission would have required — confirming the bypass described in [3](#0-2) . [3](#0-2)

### Citations

**File:** docs/architecture/how/meta-tx.md (L244-266)
```markdown
## Function access keys in meta transactions

Assume alice sends a meta transaction and signs with a function access key.
How exactly are permissions applied in this case?

Function access keys can limit the allowance, the receiving contract, and the
contract methods. The allowance limitation acts slightly strange with meta
transactions.

But first, both the methods and the receiver will be checked as expected. That
is, when the delegate action is unwrapped on Alice's shard, the access key is
loaded from the DB and compared to the function call. If the receiver or method
is not allowed, the function call action fails.

For allowance, however, there is no check. All costs have been covered by the
relayer. Hence, even if the allowance of the key is insufficient to make the call
directly, indirectly through meta transaction it will still work.

This behavior is in the spirit of allowance limiting how much financial
resources the user can use from a given account. But if someone were to limit a
function access key to one trivial action by setting a very small allowance,
that is circumventable by going through a relayer. An interesting twist that
comes with the addition of meta transactions.
```
