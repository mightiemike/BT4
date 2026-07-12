# Q2302: MintNFT owner binding against NFT owner

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then mint under an IBC-prefixed denom and send through nft-transfer, causing `NFT owner` to diverge so the invariant `recipient ownership and collection counts update atomically` fails and the attacker can overwrite an existing token id and seize or burn a victim NFT, leading to Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: overwrite an existing token id and seize or burn a victim NFT by testing the owner binding angle against `NFT owner` during `mint under an IBC-prefixed denom and send through nft-transfer`, with specific focus on owner index membership.
- Invariant to test: recipient ownership and collection counts update atomically; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: Critical loss or permanent lock of an economically valuable NFT through incorrect ownership state; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
