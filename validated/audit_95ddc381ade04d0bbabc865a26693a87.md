### Title
Cross-Task Message/Navigation Hijack via Opener-Matching Logic Flaw in ContextualTasksWindowTrackerManager::OnTabAdded - (File: chrome/browser/contextual_tasks/contextual_tasks_window_tracker_manager.cc)

### Summary
`ContextualTasksWindowTrackerManager::OnTabAdded` associates a newly opened tab with a pending `ContextualTasksWindowTracker` using opener-matching logic that treats *any* message-proxy ("guest opener") `WebContents` as a valid match for *any* pending tracker, instead of verifying that the specific opener belongs to that specific tracker. This can misroute a new tab to an unrelated task's tracker, after which cross-task navigation redirection and postMessage routing operate on the wrong tab/task pairing — a cross-context information-disclosure analog to the Aleo incident's "message delivered to the wrong recipient" failure mode.

### Finding Description
`ContextualTasksWindowTrackerManager` tracks the relationship between AI/"Contextual Tasks" `<webview>`-hosted pages and the actual browser tab/window opened via `window.open()`, using a "message proxy" `WebContents` as an opener stand-in so postMessage and navigation can be routed correctly across StoragePartitions [1](#0-0) .

When a new tab is inserted, `OnTabAdded` tries to match it to a pending tracker by comparing the new tab's opener contents against each tracker's `initiator_contents()`: [2](#0-1) 

The critical defect is in the loop condition:
```cpp
bool is_guest_opener = GuestOpenerUserData::IsGuestOpener(opener_contents);
for (const auto& tracker : window_trackers_) {
  if ((tracker->initiator_contents().get() == opener_contents ||
       is_guest_opener) &&
      !tracker->GetTabWebContents()) {
    tracker->SetTabWebContents(inserted_contents);
    return;
  }
}
```
`is_guest_opener` is computed once, globally, from `opener_contents` alone — it does not identify *which* tracker's message-proxy `WebContents` the opener corresponds to. It is `true` whenever the opener happens to be marked via `GuestOpenerUserData::IsGuestOpener()` (i.e., it is *some* Contextual Tasks message-proxy `WebContents`, not necessarily the one owned by the tracker currently being examined). Because the condition is OR'd with the correct per-tracker check, any guest-opener origin will cause the **first** tracker in `window_trackers_` that has no tab associated yet (`!tracker->GetTabWebContents()`) to be bound to the newly inserted, unrelated tab — even if that tracker belongs to a completely different task/session.

This mis-association has direct downstream consequences:
- `HandleNavigationImpl` redirects navigations from a tracked `<webview>` to `tracker->GetTabWebContents()` [3](#0-2) , so a hijacked tracker will send navigation/content intended for one task to the wrong tab.
- `GetGuestForMessage`/`GetPostMessageTargetOverride` routes postMessage traffic from a message-proxy `WebContents` to `tracker->GetInitiatorFrame()` after only checking that the sender's origin is on an allow-list of "trusted Google origins" — not that it is the specific origin tied to that tracker's task [4](#0-3) , and postMessage delivery for the hijacked mapping will be routed based on the wrong `initiator_rfh_token_`.

Since `ContextualTasksWindowTracker` instances persist task-scoped state (task IDs, expected URLs, association with a specific tab/session, potentially containing sensitive AI/"contextual" query content per the feature's own comments about tasks and sessions) [5](#0-4) , hijacking the tab association means content, navigation, or messages destined for one user task can be delivered into a differently-opened tab — a mismatch of recipient analogous to the "copy/paste error" that misdirected private documents to the wrong user in the Aleo incident.

### Impact Explanation
An attacker page participating in the Contextual Tasks flow (e.g., content rendered inside the AI/side-panel `<webview>` or a page that can trigger `window.open` with an opener that is any existing guest-opener message-proxy `WebContents`) can race a legitimate pending tracker (one that has been created via `CanCreateWindow`/`OnThreadLinkClicked` but has not yet had its tab bound, e.g., during the up-to-10-second window before `ContextualTasksWindowTracker`'s timeout timer fires) [6](#0-5) . If the attacker's own opened tab arrives before the legitimate one, `OnTabAdded`'s flawed logic binds the wrong (attacker) tab to the victim's pending tracker, causing subsequent navigation-forwarding/postMessage routing meant for the victim's task to be delivered into the attacker's tab — a cross-task/cross-tab information disclosure.

### Likelihood Explanation
Reachability requires the attacker to operate within a page that is loaded through the Contextual Tasks/AI-mode UI surface (able to call `window.open` and be recognized by `GuestOpenerUserData::IsGuestOpener`), and requires a timing race against an in-flight, not-yet-bound `ContextualTasksWindowTracker`. This narrows exploitability but does not require special privileges, flags, or MITM — only ordinary script execution from content reachable within the feature's supported flow, satisfying the "single page/script call" reachability bar.

### Recommendation
Fix the matching predicate in `OnTabAdded` to require that `opener_contents` equals the *specific* tracker's `message_proxy_web_contents()` (or its `initiator_contents()`), removing the standalone `is_guest_opener` OR-condition that lets any guest-opener match any pending tracker. Route matching should be strictly per-tracker, e.g.:
```cpp
if (tracker->message_proxy_web_contents() == opener_contents &&
    !tracker->GetTabWebContents()) {
  tracker->SetTabWebContents(inserted_contents);
  return;
}
```

### Proof of Concept
1. Legitimate flow A: User A triggers a Contextual Tasks thread-link click, creating a `ContextualTasksWindowTracker` (Task A) with a message-proxy `WebContents` set as opener for the pending new tab [7](#0-6) ; the tab has not yet loaded/bound (`!tracker->GetTabWebContents()`).
2. Attacker page B (running within the Contextual Tasks/AI-mode surface, e.g., an embedded `<webview>` page) calls `window.open()` using any handle it holds to a guest-opener message-proxy `WebContents` (any earlier-created proxy satisfies `GuestOpenerUserData::IsGuestOpener`).
3. `TabListInterfaceObserver::OnTabAdded` is invoked for the attacker's new tab; since `is_guest_opener` is `true` for its opener, and Task A's tracker has no tab yet, the loop in `OnTabAdded` binds the attacker's tab to Task A's tracker [2](#0-1) .
4. Subsequent navigation redirection (`HandleNavigationImpl`) or postMessage routing (`GetGuestForMessage`) intended for Task A is now delivered to the attacker's tab instead of User A's tab, exfiltrating task-scoped content/messages cross-context.

Confidence caveat: full exploitation requires confirming (a) that an unprivileged page within the feature's supported surface can legitimately hold a reference to *another* task's message-proxy `WebContents` object to use as `window.open`'s opener, and (b) the exact sensitivity/contents routed through `initiator_frame`/postMessage for a given task. These aspects were not independently verified from the available files and would need runtime testing to confirm full end-to-end exploitability.

### Citations

**File:** chrome/browser/contextual_tasks/contextual_tasks_ui_service.h (L240-248)
```text
  // Returns the RenderFrameHost that is tied to the `target_rfh` message proxy.
  // This is used to route messages from opened windows back to the <webview>
  // that opened them, even across separate StoragePartitions. If
  //  `target_rfh` isn't a tracked message proxy or `source_origin` isn't an
  //  allowlisted origin to send postMessages to the <webview>, then returns
  //  null.
  content::RenderFrameHost* GetGuestForMessage(
      content::RenderFrameHost* target_rfh,
      const url::Origin& source_origin);
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_window_tracker_manager.cc (L298-316)
```text
  content::RenderFrameHost* opener_rfh = inserted_contents->GetOpener();

  content::WebContents* opener_contents = nullptr;
  if (opener_rfh) {
    opener_contents = content::WebContents::FromRenderFrameHost(opener_rfh);
  }

  // Try to match by opener first.
  if (opener_contents) {
    bool is_guest_opener = GuestOpenerUserData::IsGuestOpener(opener_contents);
    for (const auto& tracker : window_trackers_) {
      if ((tracker->initiator_contents().get() == opener_contents ||
           is_guest_opener) &&
          !tracker->GetTabWebContents()) {
        tracker->SetTabWebContents(inserted_contents);
        return;
      }
    }
  }
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_ui_service.cc (L879-897)
```text
  content::WebContents::CreateParams create_params(profile_);
  std::unique_ptr<content::WebContents> message_proxy_web_contents;

  if (GetIsContextualTasksWindowTrackingEnabled() && tracker_manager_) {
    message_proxy_web_contents =
        CreateMessageProxyWebContents(initiator_origin);
    create_params.opener_id =
        message_proxy_web_contents->GetPrimaryMainFrame()->GetGlobalId();
  }

  std::unique_ptr<content::WebContents> new_contents =
      content::WebContents::Create(create_params);
  content::WebContents* new_contents_ptr = new_contents.get();
  CreateSessionServiceTabHelper(new_contents_ptr);

  if (GetIsContextualTasksWindowTrackingEnabled() && tracker_manager_) {
    tracker_manager_->MatchAndAssociatePendingTracker(
        url, new_contents_ptr, std::move(message_proxy_web_contents));
  }
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_ui_service.cc (L1998-2027)
```text
    if (!url_params.frame_tree_node_id.is_null()) {
      ContextualTasksWindowTracker* tracker =
          tracker_manager_
              ? tracker_manager_->FindTrackerByWebViewFrameTreeNodeId(
                    url_params.frame_tree_node_id)
              : nullptr;
      if (tracker) {
        content::WebContents* tab_contents = tracker->GetTabWebContents();
        if (tab_contents) {
          OMNIBOX_LOG("window_tracker")
              << "Redirecting webview navigation to tab. Task: "
              << tracker->task_id().value().AsLowercaseString()
              << ", URL: " << url_params.url.spec();
          base::SequencedTaskRunner::GetCurrentDefault()->PostTask(
              FROM_HERE,
              base::BindOnce(
                  [](base::WeakPtr<content::WebContents> wc,
                     content::OpenURLParams params) {
                    if (!wc) {
                      return;
                    }
                    content::NavigationController::LoadURLParams load_params(
                        params);
                    load_params.frame_tree_node_id = content::FrameTreeNodeId();
                    wc->GetController().LoadURLWithParams(load_params);
                  },
                  tab_contents->GetWeakPtr(), std::move(url_params)));
          return true;  // Cancel navigation in webview
        }
      }
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_ui_service.cc (L3399-3444)
```text
content::RenderFrameHost* ContextualTasksUiService::GetGuestForMessage(
    content::RenderFrameHost* target_rfh,
    const url::Origin& source_origin) {
  if (!GetIsContextualTasksWindowTrackingEnabled() || !tracker_manager_) {
    return nullptr;
  }
  // 1. Verify that the target of postMessage is one of our message proxy web
  // contents.
  content::WebContents* target_contents =
      content::WebContents::FromRenderFrameHost(target_rfh);
  if (!GuestOpenerUserData::IsGuestOpener(target_contents)) {
    OMNIBOX_LOG("route_message")
        << "GetGuestForMessage: target is not a message proxy";
    return nullptr;
  }

  // 2. Origin check: Only allow trusted Google origins to send messages.
  if (!IsAllowedOriginForGuestMessage(source_origin)) {
    OMNIBOX_LOG("route_message") << "GetGuestForMessage: origin not allowed "
                                 << source_origin.Serialize();
    return nullptr;
  }

  // 3. Find the tracker that manages the sender's tab.
  ContextualTasksWindowTracker* tracker =
      tracker_manager_->FindTrackerByMessageProxy(target_contents);
  if (!tracker) {
    OMNIBOX_LOG("route_message")
        << "GetGuestForMessage: no tracker found for message proxy";
    return nullptr;
  }

  // 4. Return the main frame that opened the frame this postMessage is coming
  // from. The postMessage will then be routed to this frame. This is most
  // likely the <webview>, but could also be an <iframe> within the <webview>.
  content::RenderFrameHost* initiator_frame = tracker->GetInitiatorFrame();
  if (initiator_frame) {
    OMNIBOX_LOG("route_message")
        << "GetGuestForMessage: found initiator frame. Routing there.";
    return initiator_frame;
  }

  OMNIBOX_LOG("route_message")
      << "GetGuestForMessage: returning nullptr at end";
  return nullptr;
}
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_window_tracker.h (L30-40)
```text
// Tracks the association between the mock <webview> window that was created in
// app.ts and the actual web contents that opened.
class ContextualTasksWindowTracker {
 public:
  ContextualTasksWindowTracker(
      const ContextualTaskId& task_id,
      const GURL& expected_url,
      content::GlobalRenderFrameHostToken initiator_rfh_token,
      base::WeakPtr<content::WebContents> webui_contents,
      base::OnceCallback<void(base::WeakPtr<ContextualTasksWindowTracker>)>
          on_closed_callback);
```

**File:** chrome/browser/contextual_tasks/contextual_tasks_window_tracker.cc (L16-35)
```text
ContextualTasksWindowTracker::ContextualTasksWindowTracker(
    const ContextualTaskId& task_id,
    const GURL& expected_url,
    content::GlobalRenderFrameHostToken initiator_rfh_token,
    base::WeakPtr<content::WebContents> webui_contents,
    base::OnceCallback<void(base::WeakPtr<ContextualTasksWindowTracker>)>
        on_closed_callback)
    : task_id_(task_id),
      expected_url_(expected_url),
      initiator_rfh_token_(initiator_rfh_token),
      webui_contents_(webui_contents),
      on_closed_callback_(std::move(on_closed_callback)) {
  timeout_timer_.Start(
      FROM_HERE, base::Seconds(10),
      base::BindOnce(&ContextualTasksWindowTracker::OnWindowClosed,
                     base::Unretained(this)));
  OMNIBOX_LOG("window_tracker")
      << "ContextualTasksWindowTracker created for task: "
      << task_id_.value().AsLowercaseString();
}
```
