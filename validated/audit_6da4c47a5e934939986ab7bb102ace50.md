### Title
Cross-Origin Password Disclosure via Attacker-Controlled `domain` Parameter in `RequestAgentAuthentication` - ([File: chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc])

### Summary
The Mojo handler `RemoteActorCredentialSharingImpl::RequestAgentAuthentication` accepts a page-supplied `domain` string that is used, unvalidated against the caller's own origin, both to query the password store and to build the `web_origin` that is ultimately shared with a remote "actor" task. This mirrors the reported bug class: a parameter that should be bound to the identity of the actual caller (like `sender` vs `msg.sender` in `updatePythPrice`) is instead fully attacker-controlled, letting the caller redirect a sensitive operation (here, credential disclosure) to a target it does not own.

### Finding Description
`RequestAgentAuthentication` is exposed over an `AssociatedReceiver` bound via `RemoteActorCredentialSharingImpl::BindReceiver`, which only checks that the calling frame's origin is on an allow‑list (`IsRemoteActorCredentialSharingAllowedForOrigin`) [1](#0-0) . Once bound, the JS-callable method takes `gaia_id`, `domain`, and `task_id` directly from the page [2](#0-1) .

`ValidateRequestPreconditions` only checks that the request comes from the primary main frame with a user gesture, that the process can access its *own* origin, and that argument lengths are bounded — it never checks that `domain` equals or is derived from `target_frame.GetLastCommittedOrigin()` [3](#0-2) .

The unvalidated `domain` is then used to build the `PasswordFormDigest` used to query the password store, i.e., it selects *which site's* saved credentials get retrieved and shown in the credential-selection dialog — completely independent of the page's real origin [4](#0-3) .

After the user picks a credential and (if required) re-authenticates, the same attacker-supplied `domain` is used to compute `params.web_origin`, which is passed to `RemoteActorCredentialSharingService::SharePassword` along with the plaintext-equivalent password specifics for that domain [5](#0-4) .

This is structurally identical to the `updatePythPrice(sender, ...)` bug: a value that determines *where sensitive output/funds/data end up* is supplied by the untrusted caller instead of being derived from the caller's authenticated identity (`msg.sender` / `target_frame.GetLastCommittedOrigin()`).

### Impact Explanation
A single script running in a frame on an allow-listed origin (the feature currently allow-lists a host such as `gemini.google.com` per the test fixture) can call `chrome.requestAgentAuthentication(gaia_id, domain, task_id, callback)` with an arbitrary `domain` value (e.g. `bank.example.com`), causing Chrome to:
1. Query the user's saved credentials for that arbitrary domain, not the calling page's own domain.
2. Present a native credential-selection dialog and (optionally) a biometric/OS re-auth prompt — plausibly interpreted by the user as authorizing the *current* site.
3. On user confirmation, package and share the selected credential's password specifics tagged with the attacker-chosen `web_origin` to the remote sharing service, i.e., disclose the credential associated with an origin the calling page does not control.

This is a cross-origin secret disclosure of stored passwords, mediated entirely through a single-page/script Mojo IPC call, with no additional privileges, flags, extensions, or MITM required beyond running on an origin where the API is already exposed.

### Likelihood Explanation
Likelihood is high given reachability: the check `IsRemoteActorCredentialSharingAllowedForOrigin` gates only which *pages* may call the API, not what `domain` value they may pass. Any script executing in an allowed frame (e.g., via a compromised/XSS'd page on the allow-listed host, or once the allow-list is expanded beyond the test-only single host) can supply any `domain`. The only friction is the requirement for a user gesture and (depending on platform config) a device re-authentication/dialog confirmation — but nothing in the confirmation UI is shown to prevent this because the flow is designed to look legitimate for "the current task."

### Recommendation
Bind `domain` to the calling frame's authenticated origin rather than trusting the caller-supplied string, analogous to enforcing `sender == msg.sender` in the reported Solidity issue:
- In `ValidateRequestPreconditions` (or immediately in `RequestAgentAuthentication`), require that `domain` equals (or is derived from) `render_frame_host().GetLastCommittedOrigin()`'s host, and `ReportBadMessage`/reject otherwise.
- Alternatively, remove the `domain` parameter entirely from the Mojo call and derive the target domain exclusively from `target_frame.GetLastCommittedOrigin()` on the browser side, exactly as the external report recommends replacing the `sender` parameter with `msg.sender`.

### Proof of Concept
```js
// Executed from any frame on an origin allowed by
// IsRemoteActorCredentialSharingAllowedForOrigin (e.g. the allow-listed host)
chrome.requestAgentAuthentication(
  myOwnGaiaId,          // must match signed-in user (validated)
  "victim-bank.com",    // ATTACKER-CHOSEN domain, unrelated to current page
  "attacker-task-id",
  (success) => {
    // On success, the credential stored for "victim-bank.com" has been
    // shared to the remote actor task keyed by attacker-task-id/gaia_id,
    // even though this script's own origin is unrelated to victim-bank.com.
  }
);
```
Root cause confirmed at: [6](#0-5) [7](#0-6)

### Citations

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L74-91)
```text
void RemoteActorCredentialSharingImpl::BindReceiver(
    mojo::PendingAssociatedReceiver<chrome::mojom::RemoteActorCredentialSharing>
        receiver,
    content::RenderFrameHost* rfh) {
  if (!rfh->IsInPrimaryMainFrame()) {
    return;
  }
  if (!IsRemoteActorCredentialSharingAllowedForOrigin(
          rfh->GetLastCommittedOrigin())) {
    rfh->GetProcess()->ShutdownForBadMessage(
        content::RenderProcessHost::CrashReportMode::GENERATE_CRASH_DUMP);
    return;
  }

  auto* impl =
      RemoteActorCredentialSharingImpl::GetOrCreateForCurrentDocument(rfh);
  impl->Bind(std::move(receiver));
}
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L122-149)
```text
void RemoteActorCredentialSharingImpl::RequestAgentAuthentication(
    const std::string& gaia_id,
    const std::string& domain,
    const std::string& task_id,
    RequestAgentAuthenticationCallback callback) {
  if (!ValidateRequestPreconditions(gaia_id, domain, task_id)) {
    RespondWithError(std::move(callback));
    return;
  }

  if (pending_request_) {
    LogResult(RemoteActorCredentialSharingResult::kRequestAlreadyInProgress);
    RespondWithError(std::move(callback));
    return;
  }

  Profile* profile =
      Profile::FromBrowserContext(render_frame_host().GetBrowserContext());

  if (!VerifyUserIdentityAndSyncState(profile, gaia_id)) {
    LogResult(
        RemoteActorCredentialSharingResult::kUserIdentityOrSyncStateInvalid);
    RespondWithError(std::move(callback));
    return;
  }

  QueryPasswordStores(profile, gaia_id, domain, task_id, std::move(callback));
}
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L259-277)
```text
  StoredCredential credential = FromPasswordForm(std::move(selected_form));
  sync_pb::PasswordSpecificsData specifics_data =
      SpecificsDataFromStoredCredential(credential);

  RemoteActorCredentialSharingService::ShareParameters params;
  params.obfuscated_gaia_id = pending_request_->gaia_id;
  params.web_origin =
      url::Origin::Create(
          GURL(base::StrCat({"https://", pending_request_->domain})))
          .Serialize();
  params.password_data = std::move(specifics_data);
  params.time_to_live = kShareTimeToLive;
  params.task_id = pending_request_->task_id;

  service->SharePassword(
      params,
      base::BindOnce(&RemoteActorCredentialSharingImpl::OnShareCompleted,
                     weak_ptr_factory_.GetWeakPtr(),
                     std::move(pending_request_->callback)));
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L280-316)
```text
bool RemoteActorCredentialSharingImpl::ValidateRequestPreconditions(
    const std::string& gaia_id,
    const std::string& domain,
    const std::string& task_id) {
  content::RenderFrameHost& target_frame = render_frame_host();

  if (!target_frame.IsInPrimaryMainFrame()) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Request from subframe");
    return false;
  }

  if (!target_frame.HasTransientUserActivation()) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Request without user gesture");
    return false;
  }

  auto* policy = content::ChildProcessSecurityPolicy::GetInstance();
  if (!policy->CanAccessDataForOrigin(
          target_frame.GetProcess()->GetID().GetUnsafeValue(),
          target_frame.GetLastCommittedOrigin())) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Process cannot access origin");
    return false;
  }

  if (gaia_id.length() >= kMaxArgumentLength ||
      domain.length() >= kMaxArgumentLength ||
      task_id.length() >= kMaxArgumentLength) {
    receiver_.ReportBadMessage(
        "RemoteActorCredentialSharing: Argument length limit exceeded");
    return false;
  }

  return true;
}
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.cc (L345-399)
```text
void RemoteActorCredentialSharingImpl::QueryPasswordStores(
    Profile* profile,
    const std::string& gaia_id,
    const std::string& domain,
    const std::string& task_id,
    RequestAgentAuthenticationCallback callback) {
  CHECK(!pending_request_);
  dialog_controller_.reset();

  auto* sync_service = SyncServiceFactory::GetForProfile(profile);
  const bool is_password_sync_active =
      sync_service &&
      sync_util::IsSyncFeatureActiveIncludingPasswords(sync_service);
  // We only query stores that contain credentials synced to the Google Account.
  // - If password sync is active (sync-the-feature), the profile store contains
  //   the synced credentials.
  // - If sync is inactive but account storage is active (sync-the-transport),
  //   the account store contains the account-scoped credentials.
  // Local-only credentials (profile store when sync is inactive) are excluded
  // from sharing.
  std::vector<PasswordStoreInterface*> stores;
  if (is_password_sync_active) {
    auto profile_store = ProfilePasswordStoreFactory::GetForProfile(
        profile, ServiceAccessType::EXPLICIT_ACCESS);
    CHECK(profile_store);
    stores.push_back(profile_store.get());
  } else if (sync_service &&
             features_util::IsAccountStorageActive(sync_service)) {
    auto account_store = AccountPasswordStoreFactory::GetForProfile(
        profile, ServiceAccessType::EXPLICIT_ACCESS);
    CHECK(account_store);
    stores.push_back(account_store.get());
  }

  if (stores.empty()) {
    LogResult(RemoteActorCredentialSharingResult::kNoSyncOrAccountStorage);
    RespondWithError(std::move(callback));
    return;
  }

  pending_request_ = PendingRequest{
      .gaia_id = gaia_id,
      .domain = domain,
      .task_id = task_id,
      .callback = std::move(callback),
      .expected_callbacks = static_cast<int>(stores.size()),
  };

  PasswordFormDigest digest(PasswordForm::Scheme::kHtml,
                            base::StrCat({"https://", domain, "/"}),
                            GURL(base::StrCat({"https://", domain})));

  for (PasswordStoreInterface* store : stores) {
    store->GetLogins(digest, weak_ptr_factory_.GetWeakPtr());
  }
```

**File:** chrome/browser/password_manager/remote_actor/remote_actor_credential_sharing_impl.h (L86-90)
```text
  void RequestAgentAuthentication(
      const std::string& gaia_id,
      const std::string& domain,
      const std::string& task_id,
      RequestAgentAuthenticationCallback callback) override;
```
