# Q2084: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then issue denom around validation boundary characters and later parse through class trace logic, causing `denom owner` to diverge so the invariant `native NFT denom state remains separate from IBC class trace state` fails and the attacker can make downstream NFT transfer treat attacker-issued denom as backed by escrow, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: make downstream NFT transfer treat attacker-issued denom as backed by escrow by testing the owner binding angle against `denom owner` during `issue denom around validation boundary characters and later parse through class trace logic`, with specific focus on denom owner authority.
- Invariant to test: native NFT denom state remains separate from IBC class trace state; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
