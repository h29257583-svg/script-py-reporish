"""Bound probe traffic and record evidence without storing cookies or response bodies."""
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import json
import threading
import time


def classify_message(message):
    text = str(message).casefold().replace('’', "'")
    if any(x in text for x in ('429', 'rate-limited', 'too many requests', 'try again later')):
        return 'rate_limited'
    if "confirm you're not a bot" in text:
        return 'botcheck'
    if 'confirm your age' in text:
        return 'age_verification'
    if 'private video' in text:
        return 'private_video'
    if 'removed by the uploader' in text or 'video has been removed' in text:
        return 'removed_video'
    if 'the channel is not currently live' in text:
        return 'not_live'
    if 'this live event will begin' in text:
        return 'upcoming'
    if 'this live event has ended' in text:
        return 'ended'
    if any(x in text for x in ('timed out', 'timeout', 'connection', 'network', '502', '503', '504')):
        return 'network_error'
    if '403' in text:
        return 'access_denied'
    return 'extraction_failed'


class ProbeHealth:
    def __init__(self, ttl=45, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.lock = threading.RLock()
        self.extraction_lock = threading.Lock()
        self.cache = OrderedDict()
        self.counts = Counter()
        self.blocked_until = 0
        self.denials = 0
        self.last = None
        self.last_success = None
        self.last_reason = None

    def observe(self, reason):
        with self.lock:
            self.counts[reason] += 1
            self.last_reason = reason
            self.last = datetime.now(timezone.utc).isoformat(timespec='seconds')
            if reason in ('live', 'not_live', 'upcoming', 'ended', 'not_live_metadata'):
                self.last_success = self.last
            if reason in ('rate_limited', 'botcheck', 'access_denied'):
                self.denials += 1
                wait = min(3600, 300 * 2 ** min(self.denials-1, 4))
                self.blocked_until = max(self.blocked_until, self.clock()+wait)
            elif reason == 'live':
                self.denials = 0

    def permitted(self):
        with self.lock:
            return self.clock() >= self.blocked_until

    def call(self, key, loader):
        # Serialize player extraction. A second detector must not duplicate an in-flight call.
        with self.extraction_lock:
            with self.lock:
                hit = self.cache.get(key)
                if hit and self.clock()-hit[0] < hit[1]:
                    self.counts['cache_hits'] += 1
                    return hit[2]
                if not self.permitted():
                    self.counts['cooldown_skips'] += 1
                    return None
            value, reason = loader()
            self.observe(reason)
            ttl = 300 if reason in ('ended', 'private_video', 'removed_video') else self.ttl
            with self.lock:
                self.cache[key] = (self.clock(), ttl, value)
                self.cache.move_to_end(key)
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
            return value

    def snapshot(self):
        with self.lock:
            return {'counts':dict(self.counts), 'last_probe_utc':self.last,
                    'last_successful_probe_utc':self.last_success, 'last_reason':self.last_reason,
                    'probe_cooldown_seconds':round(max(0,self.blocked_until-self.clock())),
                    'offline_is_not_inferred_from_network_or_auth_errors':True}


def _json_assignment(text, variable):
    # YouTube embeds complete objects; brace-counting regexes break on braces in strings.
    import re
    decoder = json.JSONDecoder()
    pattern = r'(?:var\s+)?'+re.escape(variable)+r'\s*=\s*'
    for match in re.finditer(pattern, text):
        try:
            value,_ = decoder.raw_decode(text[match.end():])
            if isinstance(value,dict):yield value
        except (ValueError,RecursionError):
            continue


def live_badge_ids(html):
    """Associate LIVE badges with their own renderer, never a nearby unrelated video."""
    found = []
    def walk(node):
        if isinstance(node,list):
            for item in node:walk(item)
        elif isinstance(node,dict):
            for key,value in node.items():
                if key in ('videoRenderer','gridVideoRenderer','reelItemRenderer') and isinstance(value,dict):
                    video = value.get('videoId')
                    flags = json.dumps({k:v for k,v in value.items() if k in ('badges','thumbnailOverlays','upcomingEventData')},separators=(',',':'))
                    live = 'BADGE_STYLE_TYPE_LIVE_NOW' in flags or '"style":"LIVE"' in flags
                    if video and live and not value.get('upcomingEventData') and video not in found:
                        found.append(video)
                walk(value)
    for value in _json_assignment(html,'ytInitialData'):walk(value)
    # A /live endpoint may expose its own currently-live player without a videoRenderer.
    for value in _json_assignment(html,'ytInitialPlayerResponse'):
        details=value.get('videoDetails',{})
        live=value.get('microformat',{}).get('playerMicroformatRenderer',{}).get('liveBroadcastDetails',{})
        if live.get('isLiveNow') is True and details.get('videoId') and details['videoId'] not in found:
            found.append(details['videoId'])
    return found
