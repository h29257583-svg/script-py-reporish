# Validation evidence — 25 October 2026

## Verified

- 57 local regression tests pass, including concurrent probe suppression, denial backoff, honest metadata classification, short recovery retention, remote receipt verification, checkpoint recovery, local metadata selection, and private helper owner checks.
- Controlled 720p HLS test: a 30-second HTTP 503 interruption preserves all 864/864 frames over 72 seconds; full audio/video decode passes.
- Controlled overlapping handoff test: all 1,200/1,200 unique frames across the combined 100-second interval are present, with no missing frames, despite the same 30-second interruption. Overlap intentionally retains duplicate footage.
- Real GitHub validation in py-task-runner captured SkyNews, fully decoded the result and uploaded it to Drive with verified size/MD5. Rolling safety copies also uploaded. The corrected folder-link validation passed: https://github.com/gauravdev833844-gif/py-task-runner/actions/runs/40172669453 .
- Historical overnight audit found 25 uploaded files totaling 24,932,801,768 bytes, including duplicate overlap and recovery clips. One verified Drive file exceeded 5.6 GB. A green workflow alone does not establish uninterrupted capture.

## Failed or unresolved

- Several other repositories passed config format and real Drive readback but failed actual live capture. Explicit bot checks and incomplete player metadata were observed; these are not proven offline streamers. Do not label these repositories ready based on successful Drive tests.
- Cached-format capture also passed a later real py-task-runner check: https://github.com/gauravdev833844-gif/py-task-runner/actions/runs/40175051461 . It produced 110 seconds of 720p video (including the available HLS lookback), verified the final Drive copy and seven safety clips. H23 Flox with the same code failed capture while Drive readback passed: https://github.com/h29257583-svg/py-task-floxrepo/actions/runs/40175057219 . Later changes make extraction failures explicit; they do not repair rejected authorization.
- Johnwatson's six repositories are readable but the current app installation does not authorize code/workflow writes. Collaboration invitation acceptance alone did not enable those API writes.
- No actual nine-hour v3 run, 15-channel concurrent run, or Telegram send has been verified. No guarantee can be made for source access, provider outages, deleted/private footage, or future account enforcement.

Controlled tests establish the implemented recovery behavior. They do not reproduce every YouTube/CDN failure or prove 100% capture probability.
