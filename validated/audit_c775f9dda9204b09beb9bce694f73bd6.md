## Title
Cross-window `postMessage` re-routing in Contextual Tasks trusts source-origin allowlist instead of the actual initiator, letting any window on a "trusted" origin spoof messages to the tracked initiator frame - ([File: chrome/browser/contextual_tasks/contextual_tasks_ui_service.cc])

## Summary
`ContextualTasksUiService::GetGuestForMessage()` is the browser-side hook (`ChromeContentBrowserClient::GetPostMessageTargetOverride`) that decides where a `window.postMessage()` call targeting the Contextual Tasks "dummy opener" `WebContents` actually gets delivered. Like the Astaria `flashAction()` callback, which invokes `receiver.onFlashAction()` without telling the recipient *who* initiated the call, this code re-routes a cross-window message to `tracker->GetInitiatorFrame()` after checking only that (a) the target is a registered `GuestOpenerUserData` proxy and (b) the *source origin* is on a coarse allowlist (`IsAllowedOriginForGuestMessage`). It never verifies that the specific sending window/frame is the one actually associated with that tracker session, so the receiving frame has no way to authenticate the true initiator of the message.

## Finding Description
`ChromeContentBrowserClient::GetPostMessageTargetOverride` is invoked by the browser's postMessage delivery path whenever a document calls `postMessage` on a target whose `WebContents` is tagged `GuestOpenerUserData` (used to route `window.open()`-originated postMessages back into an embedded `<webview>`): [1](#0-0) 

It delegates the actual routing decision to `ContextualTasksUiService::GetGuestForMessage`, which performs only two checks before returning an override target:
1. the postMessage *target* `WebContents` is a `GuestOpenerUserData` proxy,
2. the *source origin* passes `IsAllowedOriginForGuestMessage` (a "trusted Google origins" allowlist). [2](#0-1) 

After these two checks it looks up the tracker keyed only by the message-proxy `WebContents` pointer (`FindTrackerByMessageProxy`) and unconditionally returns `tracker->GetInitiatorFrame()` as the new destination for the message — the real page/tab that originally opened the Contextual Tasks flow, e.g. the user's active tab. There is no per-message credential (token, nonce, or verification that the sending `RenderFrameHost` is the specific window the tracker created) tying the sender to that particular tracker/session; only origin membership in a broad allowlist and the identity of the *target* proxy `WebContents` are checked. The `ContextualTasksWindowTracker` does hold an `initiator_rfh_token_` for the window that triggered the open, but `GetGuestForMessage` never compares the sending frame/token against it — it is used only for other bookkeeping (tab-association matching), as seen in the tracker manager's opener-matching logic: [3](#0-2) 

The interactive test `PopupPostMessageToOpenerRoutedToWebview` demonstrates the exact mechanics: a popup opened from within the AI/companion `<webview>` navigates to an arbitrary same-allowlisted-origin URL (`myaccount.google.com/title1.html#citation`) and then simply calls `window.opener.postMessage("hello_from_opened_page", "*")`; the browser reroutes this into the guest `<webview>`'s window as if it came from the legitimate companion flow: [4](#0-3) [5](#0-4) 

This is structurally the same flaw as the Astaria report: the recipient of a re-dispatched callback/message is given no way to authenticate which specific caller triggered the interaction — only a coarse, origin-level allowlist stands in for real initiator verification. `GuestOpenerUserData::IsGuestOpener` itself is a trivial boolean tag with no session binding at all: [6](#0-5) 

## Impact Explanation
If any page navigated under an origin covered by `IsAllowedOriginForGuestMessage` (a broad, Google-domain-level allowlist rather than a specific per-session origin) ends up as `window.opener` of a `GuestOpenerUserData` proxy `WebContents` — e.g., via a popup spawned from within the guest `<webview>` that subsequently navigates cross-document, or any other flow that lets an attacker-influenced document become associated with that proxy — it can inject arbitrary `postMessage` payloads into the tracked initiator frame (the user's real browser tab) or into the companion `<webview>`, impersonating a trusted party in the Contextual Tasks / AI companion channel. Because the initiator frame's message handler presumably trusts messages delivered through this special browser-mediated channel (it is not ordinary content-to-content `postMessage`, it is a browser-privileged reroute), this can enable cross-origin message spoofing / a confused-deputy channel between an attacker-influenced document and privileged browser-feature surfaces, which maps to a SOP-adjacent message-spoofing primitive.

## Likelihood Explanation
The routing path is reachable purely from web content: any site loaded as (or navigated within) the Contextual Tasks guest `<webview>`/popup chain can call ordinary `window.opener.postMessage()` with `"*"` as `targetOrigin`, exactly as shown in the existing browsertest. No extensions, WebUI-only access, flags, or local access are required — only that the calling document's origin passes the allowlist check and that it can obtain a reference to the dummy opener `WebContents` created for `window.open()` flows, which is a normal part of the feature's designed window-opening flow.

## Recommendation
Do not rely solely on origin allowlisting to authorize the reroute. `GetGuestForMessage` (and `IsAllowedOriginForGuestMessage`) should verify that the sending `RenderFrameHost`/its `GlobalRenderFrameHostToken` matches the specific `initiator_rfh_token_`/window that `ContextualTasksWindowTracker` recorded for that session (analogous to passing the "initiator" through the callback so the recipient can authenticate it), rather than accepting any frame whose origin merely appears on a shared allowlist and whose target proxy pointer matches.

## Proof of Concept
The existing browsertest `PopupPostMessageToOpenerRoutedToWebview` in `contextual_tasks_interactive_uitest.cc` already exercises the primitive end-to-end: it opens the Contextual Tasks flow, spawns a popup from the guest `<webview>` navigated to an allowlisted-origin page, and has that popup call `window.opener.postMessage("hello_from_opened_page", "*")`, which is delivered by `ContextualTasksUiService::GetGuestForMessage` into the guest `<webview>`'s window listener — demonstrating that message delivery is authorized purely by target-proxy identity + origin allowlist, not by verification of the actual initiating window/session. [7](#0-6)

### Citations

**File:** chrome/browser/chrome_content_browser_client.cc (L8877-8912)
```text
content::RenderFrameHost*
ChromeContentBrowserClient::GetPostMessageTargetOverride(
    content::RenderFrameHost* target_rfh,
    const std::optional<blink::LocalFrameToken>& source_frame_token,
    const url::Origin& source_origin,
    const std::optional<url::Origin>& target_origin) {
  content::WebContents* web_contents =
      content::WebContents::FromRenderFrameHost(target_rfh);
  if (!web_contents) {
    return nullptr;
  }

  // Don't proceed to looking up the ContextualTasksUiService if the WebContents
  // is not marked as a guest opener. In other words, this post message is
  // unrelated to Contextual Tasks.
  if (!contextual_tasks::GuestOpenerUserData::IsGuestOpener(web_contents)) {
    return nullptr;
  }

  Profile* profile =
      Profile::FromBrowserContext(web_contents->GetBrowserContext());
  if (!profile) {
    return nullptr;
  }

  // The ContextualTasks feature manually tracks window opens to be able to
  // route messages back to the appropriate RenderFrameHost. If that feature
  // returns a frame, use that instead.
  contextual_tasks::ContextualTasksUiService* service = contextual_tasks::
      ContextualTasksUiServiceFactory::GetForBrowserContextIfExists(profile);
  if (service) {
    return service->GetGuestForMessage(target_rfh, source_origin);
  }

  return nullptr;
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

**File:** chrome/browser/contextual_tasks/contextual_tasks_interactive_uitest.cc (L2313-2408)
```text
                       PopupPostMessageToOpenerRoutedToWebview) {
  const GURL kActiveTabUrl =
      https_server_.GetURL("myaccount.google.com", "/title1.html");
  const GURL clicked_url =
      https_server_.GetURL("myaccount.google.com", "/title1.html#citation");
  const GURL kInterceptionUrl =
      https_server_.GetURL(kMockAimPageHost, "/search?udm=50");

  DEFINE_LOCAL_ELEMENT_IDENTIFIER_VALUE(kOpenedPopup);
  DEFINE_LOCAL_ELEMENT_IDENTIFIER_VALUE(kPrimaryTab2);
  DEFINE_LOCAL_ELEMENT_IDENTIFIER_VALUE(kInnerWebContentsId2);

  // Step 1: Open Contextual Tasks in a Tab
  auto sequence =
      Steps(InstrumentTab(kPrimaryTab, 0), SelectTab(kTabStripElementId, 0),
            OpenContextualTasksInCurrentTab(kInterceptionUrl),
            InstrumentInnerWebContents(kInnerWebContentsId, kPrimaryTab, 0));

  sequence = Steps(
      std::move(sequence),
      // 1. Inject listener in the <webview> to collect
      // received messages.
      WithElement(kInnerWebContentsId,
                  base::BindOnce([](ui::TrackedElement* el) {
                    std::string setup_listener = R"(
            window.receivedMessages = [];
            window.messagePromiseResolver = null;
            window.addEventListener('message', (event) => {
              window.receivedMessages.push(event.data);
              if (window.messagePromiseResolver &&
                  event.data === 'hello_from_opened_page') {
                window.messagePromiseResolver(true);
              }
            });
          )";
                    auto* wc = AsInstrumentedWebContents(el)->web_contents();
                    EXPECT_TRUE(content::ExecJs(wc, setup_listener));
                  })),

      // 2. Set up instrumenting the next tab BEFORE we trigger window.open to
      // avoid race conditions. We use AnyBrowser() to catch it if it opens in
      // a new window.
      InstrumentNextTab(kOpenedPopup, AnyBrowser()),

      // 3. Within the guest view, call window.open with popup=yes
      WithElement(kInnerWebContentsId,
                  base::BindOnce(
                      [](GURL url, ui::TrackedElement* el) {
                        auto* wc =
                            AsInstrumentedWebContents(el)->web_contents();
                        std::string open_script = content::JsReplace(
                            R"(
                  (() => {
                    window.open($1, '_blank', 'popup=yes,width=400,height=400');
                  })();
                )",
                            url.spec());
                        EXPECT_TRUE(content::ExecJs(wc, open_script));
                      },
                      clicked_url)),

      // 4. Wait for the opened popup to finish loading
      WaitForWebContentsNavigation(kOpenedPopup, clicked_url),

      // Check that the popup window is focused when it is opened.
      CheckElement(
          kOpenedPopup, base::BindOnce([](ui::TrackedElement* el) {
            auto* wc = AsInstrumentedWebContents(el)->web_contents();
            tabs::TabInterface* tab = tabs::TabInterface::GetFromContents(wc);
            BrowserWindowInterface* popup_browser =
                tab->GetBrowserWindowInterface();
            EXPECT_TRUE(popup_browser);
            EXPECT_TRUE(ui_test_utils::IsBrowserActive(popup_browser));
            return true;
          })),

      // 5. Verify window.opener is non-null and call postMessage from the
      // opened popup
      WithElement(kOpenedPopup, base::BindOnce([](ui::TrackedElement* el) {
                    auto* wc = AsInstrumentedWebContents(el)->web_contents();
                    std::string post_message_script = R"(
            (async () => {
              if (!window.opener) {
                return "no opener";
              }
              try {
                window.opener.postMessage("hello_from_opened_page", "*");
                return "ok";
              } catch (e) {
                return "error: " + e.message;
              }
            })();
          )";
                    EXPECT_EQ("ok", content::EvalJs(wc, post_message_script));
                  })));

```

**File:** chrome/browser/contextual_tasks/guest_opener_user_data.h (L12-26)
```text
// A generic C++ tag class used to mark a WebContents as a dummy opener
// designed to intercept and route window.open postMessages to guest views.
class GuestOpenerUserData
    : public content::WebContentsUserData<GuestOpenerUserData> {
 public:
  ~GuestOpenerUserData() override;

  static bool IsGuestOpener(const content::WebContents* web_contents);

 private:
  explicit GuestOpenerUserData(content::WebContents* contents);
  friend class content::WebContentsUserData<GuestOpenerUserData>;

  WEB_CONTENTS_USER_DATA_KEY_DECL();
};
```
