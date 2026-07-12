# Q2205: IssueDenom owner binding against denom owner

## Question
Can unprivileged account enter through `msgServer.IssueDenom` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Id, Name, Schema, focusing on authz signer semantics, under the precondition that a valuable denom or NFT exists under the NFT module, then issue a denom then use authz or feegrant to mint from that denom, causing `denom owner` to diverge so the invariant `only the denom owner can mint NFTs under that denom` fails and the attacker can make downstream NFT transfer treat attacker-issued denom as backed by escrow, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.IssueDenom
- Entrypoint: Cosmos SDK MsgIssueDenom transaction
- Attacker controls: Sender, Id, Name, Schema, Uri, address prefix
- Exploit idea: make downstream NFT transfer treat attacker-issued denom as backed by escrow by testing the owner binding angle against `denom owner` during `issue a denom then use authz or feegrant to mint from that denom`, with specific focus on authz signer semantics.
- Invariant to test: only the denom owner can mint NFTs under that denom; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over IssueDenom, MintNFT, IBC class hash, and collection lookup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
