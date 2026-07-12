# Q2336: MintNFT owner binding against collection

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then mint token ids that alias after validation or storage key composition, causing `collection` to diverge so the invariant `only denom owner can mint under a denom` fails and the attacker can use signer confusion to mint with a grantee but charge or authorize a different account, leading to Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: use signer confusion to mint with a grantee but charge or authorize a different account by testing the owner binding angle against `collection` during `mint token ids that alias after validation or storage key composition`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: only denom owner can mint under a denom; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical unauthorized NFT mint, burn, transfer, denom takeover, or seizure of a valuable NFT; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
