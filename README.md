# Recorder recovery and Drive upload validation

This branch contains a portable capture engine and bounded software CI checks. It is not a promise of uninterrupted daily hosting. Keep production archives on a runtime whose terms permit them; GitHub Actions is limited to development/testing and must not be treated as a free general-purpose media server.

## Changes

- Thirty-second primary detection, bounded secondary checks, serialized/cached player requests and explicit authentication/error states. A network failure or incomplete metadata does not become "streamer offline".
- Authenticated format metadata is reused for the first capture instead of immediately extracting it again. Signed media URLs and credentials stay in private temporary files.
- Streamlink HLS recovery tolerates temporary network/5xx failures for a bounded period, respects Retry-After, and stops on 401/403/429. A 20–30 second streamer outage is allowed within the 180-second offline grace period. Recovery still depends on segments remaining available at the source.
- Short unique recovery pieces are retained; byte-identical copies are uploaded once. Existing chunks are deleted only after a successful local merge.
- During capture, five-minute safety copies with fifteen-second overlap upload to Drive independently. Verified safety copies protect some footage even if a later final upload fails. Original capture files are preserved.
- Final upload order remains Drive, then Gofile, then optional Release fallback. Drive receipts require remote size and MD5 verification. All shifts share one session folder link rather than a separate Telegram link for every piece.

## Development

Python 3.11+, Node 22+, ffmpeg/ffprobe, curl and rclone are required. Install `requirements.txt` and `requirements-dev.txt` in a private virtual environment.

```sh
python -m unittest test_reliability test_retry_backoff test_daily_reliability test_private_setup_helper -q
python test_network_blink.py
python test_live_handoff.py
```

The last two commands serve generated HLS on localhost; they make no YouTube requests. They verify full decoded frames across a real 30-second HTTP interruption and overlapping capture processes. Bounded GitHub validation runs must additionally prove real capture, decode, and Drive readback for each repository. A format-only cookie check cannot establish usable YouTube access.

## Private configuration

`YT_COOKIES` is the complete legitimate Netscape YouTube cookie file, not a password. `RCLONE_CONFIG` is the complete rclone config for remote `drive`, not just the OAuth client ID/secret. Store them only in protected secret fields. The connector cannot write repository secrets: GitHub's Secrets API returns 403.

For a personal Google Drive OAuth app, finish Branding and publish the app to Production, then reconnect rclone once and update the repository secret with the refreshed full config. Testing refresh tokens normally expire after seven days for Drive permissions. Production is not itself a guarantee against future token revocation.

`update_private_secrets.py` is an optional owner-PC helper. It validates local files, requires GitHub CLI to be logged in as the selected repository owner, and pipes values privately to GitHub CLI. `--dry-run` lists intended secret names only. Never send the config, cookies, or bot tokens in chat. Existing secrets are left alone unless their corresponding local input is supplied.

`check_telegram.py` uses getMe/getChat only; it sends no message and does not establish message delivery. `notify_telegram.py` is the separate, optional notification sender.

## Runtime limits

The old overlapping-shift workflow is retained for comparison; the new engine is being validated on this branch and is not silently substituted into existing overnight sessions. No daily session is started by installation. A suitable always-online host and working source authorization are prerequisites. With no such host available, code changes alone cannot guarantee this workload at zero cost.

See VALIDATION.md and CAPACITY_AND_LIMITS.md for evidence and unresolved limits.
