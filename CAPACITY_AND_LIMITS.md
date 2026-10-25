# Capacity and operational limits

Drive storage capacity and upload throughput are different. All repositories using one Drive authorization share that account's storage/API limits. The user's successful rclone about reports 5 TiB. This is not an upload-speed guarantee. Google Workspace's documented 750 GB/day rule should not be presented as a verified consumer Gemini/Jio quota.

Safety copies add upload traffic. At a five-minute interval with fifteen-second overlap, their payload is about 105% of the source duration; keeping the final recording adds another copy. Three-hour windows with one-hour overlap produce twelve hours of footage per continuously live channel across a nine-hour monitoring session. At 2 Mbit/s, this is roughly 10.8 GB final footage per channel, or 162 GB for fifteen, before safety copies and retries. These are projections, not tested capacity.

Small temporary safety copies are deleted only after remote verification. The original capture remains local until its runtime is removed. The uploader's budget, Drive quota, local disk, and ongoing network throughput can still be exhausted. The code cannot retain a local-only file after an ephemeral runner is deleted.

GitHub Actions has a six-hour job ceiling and is intended for development/testing, not a general-purpose daily media service: https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions . More accounts, IP changes, or more repositories are not a supported way to evade limits or suspensions.

No verified zero-cost, always-online host for fifteen simultaneous nightly YouTube recordings is available in this setup. Render free services sleep and have ephemeral storage; current Hugging Face compute Space creation requires a paid plan; OCI requires payment verification. Do not promise free unattended production from these alternatives.

Google OAuth refresh tokens in external Testing normally expire after seven days for Drive permissions. Publish appropriately and reconnect afterward, then store the complete refreshed config privately. Later revocation or account-security changes can still require reauthorization.

Source access remains conditional: working cookies may expire or be rejected by YouTube, and private/deleted footage cannot be recreated from an unavailable source. Stop and report explicit denials; do not rotate accounts or proxies to bypass them.
