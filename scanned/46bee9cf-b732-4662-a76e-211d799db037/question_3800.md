# Q3800: Nil or empty recovery fields hash the wrong semantics together via Two Logically Distinct Events / Different Observers May Supply in GetOutboundRevertId

## Question
Can an unprivileged attacker enter through a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id with two logically distinct events represented with formatting variants when different observers may supply formatting variants of the same logical event, and cause `GetOutboundRevertId` to return the wrong live object for attacker-controlled identifiers, so that it abuse a digest rule that treats distinct recovery choices as identical, breaking the invariant that key derivation must preserve every field that affects who can reclaim value, and resulting in Wrong-party refund or permanent freezing of funds?

## Target
- File/function: x/uexecutor/types/keys.go::GetOutboundRevertId
- Entrypoint: a user-controlled deposit, payload, outbound, or observation that feeds a derived key or id
- Attacker controls: two logically distinct events represented with formatting variants
- Exploit idea: Cause `GetOutboundRevertId` to return the wrong live object for attacker-controlled identifiers, so it can abuse a digest rule that treats distinct recovery choices as identical.
- Invariant to test: key derivation must preserve every field that affects who can reclaim value
- Expected Immunefi impact: Wrong-party refund or permanent freezing of funds
- Fast validation: write a focused Go test that constructs the two crafted inputs and compare the derived ids plus the downstream replay/finalization behavior
