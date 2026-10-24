# Manual YouTube recorder with Drive-first uploads

One repository records one channel. The connected account has five repositories; the other two accounts are not accessible from this session. Setup is manual-start only. No overnight recording or Telegram message is started by installation.

## Upload order

1. **Google Drive**, when private `RCLONE_CONFIG` is configured. Copy uses rclone and verifies remote byte size and MD5. Drive files remain private; the uploader does not create public sharing permissions.
2. **Gofile** if Drive is absent or fails. Uses the standard upload endpoint; no server/IP rotation. Verifies provider byte size and MD5 acknowledgement.
3. **GitHub Release backup**, enabled by default for failed Gofile uploads. Public-repo Release assets are public. Adaptive splitting checks every part is below 2,000,000,000 bytes. Set `release_backup: false` to disable it.

A shared **two-hour upload budget** applies across all files and destinations. A Drive copy attempt may use up to 90 minutes, leaving time for fallback. With Release backup enabled, Gofile gets one attempt of up to 10 minutes and retains a minimum 10-minute backup reserve. Upload speed/completion is not guaranteed.

## Manual timing

Default `total_hours` is **9**, maximum 9. Select **8** if desired; use **0.1** for a six-minute bounded test on a channel actually live. Leave internal continuation fields blank/zero.

To reserve more upload time, recording windows are now **3 hours**, starting every **2 hours**, with one-hour overlap. For a nine-hour session:

| Shift | Start after manual run | Recording ends after manual run |
| --- | --- | --- |
| 1 | 0 h | 3 h |
| 2 (automatic) | 2 h | 5 h |
| 3 (automatic) | 4 h | 7 h |
| 4 (automatic) | 6 h | 9 h |

For eight hours, the fourth window stops at hour eight. Each prior upload job runs independently while later recorders continue. Upload may continue after the monitoring deadline. The monitoring deadline is a hard stop even if a live stream is still running. Queues/authentication/network problems can cause gaps despite planned overlap.

## Required private secrets

Repository → Settings → Secrets and variables → Actions:

- `YT_COOKIES`: complete Netscape cookie-file contents. Existing working bindings are kept. The uploaded file differs from the older working GitHub binding; historical live tests validate the existing binding only.
- `RCLONE_CONFIG`: complete rclone configuration for a remote named **drive** linked to your chosen Google account. A desktop Google Drive app login is not this authorization.
- Optional `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` for text notifications; no Telegram message was sent during setup.
- Optional `GOFILE_TOKEN`; optional repository variable `GOFILE_FOLDER_ID`. Anonymous uploads do not require them.

The GitHub integration receives **HTTP 403 on the Secrets public-key API**, so it can install code but cannot save the supplied cookie file or Google credentials as repository secrets. Enter missing values in GitHub’s secret fields, never in chat/source files.

## Own Google OAuth client (required in 2026)

Rclone’s shared Drive client is being retired during 2026. At **Continue using the shared client_id anyway?**, select **n**. Follow https://rclone.org/drive/#making-your-own-client-id .

Open https://console.cloud.google.com/ , create a project such as `youtube-recorder`, and enable **Google Drive API**. Google Cloud Storage is a different service; do not create a storage bucket or sign up for a paid VM/free-trial billing flow for this. Configure the Google Auth consent screen for your personal app, add only the `https://www.googleapis.com/auth/drive.file` scope needed by this setup, and create an OAuth client of type **Desktop app**. Enter that client ID and secret into rclone privately, then do browser authorization with the storage account.

A Google OAuth app left in **Testing** typically has seven-day refresh-token expiry for this use. Before treating unattended access as persistent, complete the appropriate personal-app production setup under Google’s OAuth requirements. This is separate from purchasing Drive storage. Do not send client secrets, tokens, or configuration values in chat.

## One-time Windows Drive connection

1. Download the Windows AMD64 ZIP from https://rclone.org/downloads/ and extract it. No card is required for rclone.
2. Open PowerShell in the extracted folder and run `.\rclone.exe config`.
3. Choose **New remote**, name it **drive**, select **Google Drive** as storage. Use your own Google OAuth Desktop client ID/secret. The shared rclone client is being retired during 2026; do not continue with a blank shared client for unattended recording.
4. Choose scope **Access to files created by rclone only (`drive.file`)**. Existing unrelated Drive files remain outside this scope. Leave service-account file blank; decline advanced settings; accept browser login.
5. In the browser choose the Google account with your storage, authorize, then return to rclone. Select no shared/team Drive unless you specifically use one; save the remote and quit.
6. Run `.\rclone.exe about drive:` to check the account’s reported capacity. This does not establish upload speed.
7. Run `.\rclone.exe config show drive | clip` to copy this remote’s config privately to your clipboard. Paste directly into GitHub secret **RCLONE_CONFIG**, starting with py-task-runner. After saving it, use Actions → **Test Google Drive upload** to verify one private generated-file upload/readback, without recording or Telegram messages. Do not paste the output/config into chat.
8. Use the same config in the other four connected repos after a real Drive test succeeds. All repositories can use one Drive account, with separate repository/run folders; its quota is shared. A second Google account is optional and needs its own separate authorization. Do not use account rotation to evade limits.

## Check setup before recording

Actions → **Check recorder setup** → Run workflow. It reports presence/format only; it does not print cookies/tokens or claim authentication success. Missing secrets make this check fail visibly.

Then run one **0.1-hour** real test. Google Drive OAuth, the claimed storage allowance and actual Drive upload remain unverified until that connection/test. Pixeldrain is not enabled because no API key is available.

## Reliability and limits

SIGINT-first shutdown preserves fragments. Low disk is reported as incomplete recording. Recovered/short chunks are probed rather than discarded merely by name or size. Receipts are checkpointed and source files retained locally. If every destination fails and a hosted runner is deleted, local-only media can still be lost. This is not an upload-while-recording implementation.

GitHub-hosted jobs have a six-hour ceiling and account-wide quotas. The workflow reserves three recording hours and two upload hours plus setup/cleanup margin. Provider terms and free-plan suitability remain separate from code correctness; do not treat more repositories/accounts as extra allowed quota or a suspension workaround.

Google Drive space is not unlimited upload throughput. Actual account quota must be checked after OAuth. See https://developers.google.com/workspace/drive/api/guides/limits and https://rclone.org/drive/ .

See VALIDATION.md: 30 local/GitHub tests, real small draft Release upload/download/checksum and earlier six-minute Gofile test passed. Real Google Drive access, full overnight handovers, five simultaneous channels and multi-GB uploads remain unverified.
