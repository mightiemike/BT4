# Q2304: MintNFT owner binding against NFT owner

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then mint via authz/feegrant path and compare signer to denom owner, causing `NFT owner` to diverge so the invariant `only denom owner can mint under a denom` fails and the attacker can create inconsistent owner indexes that allow duplicate transfers or burns, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: create inconsistent owner indexes that allow duplicate transfers or burns by testing the owner binding angle against `NFT owner` during `mint via authz/feegrant path and compare signer to denom owner`, with specific focus on owner index membership.
- Invariant to test: only denom owner can mint under a denom; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
