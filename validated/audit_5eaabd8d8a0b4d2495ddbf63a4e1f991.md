No vulnerability found for this question.

**Rationale:**

`voting::vote` accumulates votes as `proposal.yes_votes += (num_votes as u128)` / `proposal.no_votes += (num_votes as u128)` [1](#0-0) , and `get_proposal_state` reads `yes_votes + no_votes >= proposal.min_vote_threshold` [2](#0-1) .

Two independent facts defeat the proposed exploit:

1. **Move arithmetic does not wrap on overflow.** Both `+=` in `vote` and the `yes_votes + no_votes` addition in `get_proposal_state` are checked u128 additions in the Move VM/bytecode verifier semantics — an overflow triggers a deterministic arithmetic abort of the transaction rather than silently wrapping. This means even a hypothetical near-`u128::MAX` sum cannot produce a corrupted, wrapped value read by `get_proposal_state`; the offending `vote` call (or the view call, if it could ever overflow) would simply abort, leaving `yes_votes`/`no_votes` unchanged and the ledger state uncorrupted.

2. **`num_votes` is not attacker-controlled to arbitrary size.** `num_votes` is a `u64` supplied by the caller of `vote`, but `vote` requires `_proof: &ProposalType`, a capability that only the governance module defining `ProposalType` can construct [3](#0-2) . In `aptos_governance`, the voting power passed as `num_votes` is derived from real staked/delegated coin amounts, which are themselves bounded by the `u64` total coin supply of APT — nowhere near the ~`2^128` total votes that would be required across all calls to approach `u128::MAX`. Reaching an overflow boundary is computationally and economically infeasible given real stake bounds, independent of the checked-arithmetic protection above.

Because Move's checked arithmetic aborts on overflow (no silent wraparound) and the practical vote magnitudes are bounded far below `u128::MAX` by real coin supply, there is no path for unprivileged `vote` calls to corrupt `proposal.yes_votes`/`no_votes` into a state where `get_proposal_state` returns an inconsistent `PROPOSAL_STATE_SUCCEEDED`/`PROPOSAL_STATE_FAILED` relative to true vote totals. This does not meet the State-Integrity Gate's requirement of unprivileged input corrupting committed state or an authenticated response.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/voting.move (L350-356)
```text
    public fun vote<ProposalType: store>(
        _proof: &ProposalType,
        voting_forum_address: address,
        proposal_id: u64,
        num_votes: u64,
        should_pass: bool,
    ) acquires VotingForum {
```

**File:** aptos-move/framework/aptos-framework/sources/voting.move (L373-377)
```text
        if (should_pass) {
            proposal.yes_votes += (num_votes as u128);
        } else {
            proposal.no_votes += (num_votes as u128);
        };
```

**File:** aptos-move/framework/aptos-framework/sources/voting.move (L590-599)
```text
        if (is_voting_closed<ProposalType>(voting_forum_address, proposal_id)) {
            let proposal = get_proposal<ProposalType>(voting_forum_address, proposal_id);
            let yes_votes = proposal.yes_votes;
            let no_votes = proposal.no_votes;

            if (yes_votes > no_votes && yes_votes + no_votes >= proposal.min_vote_threshold) {
                PROPOSAL_STATE_SUCCEEDED
            } else {
                PROPOSAL_STATE_FAILED
            }
```
