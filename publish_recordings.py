"""Bounded, checksummed uploads. No raw provider responses or credentials logged."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import quote, urlparse

RELEASE_LIMIT = 2_000_000_000  # Strictly below GitHub's 2 GiB ceiling.
GOFILE_ENDPOINT = 'https://upload.gofile.io/uploadfile'


class PublishError(Exception):
    pass


def fingerprints(path, deadline=None):
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            if deadline is not None and time.monotonic() >= deadline:
                raise PublishError('upload_budget_exhausted')
            md5.update(data)
            sha256.update(data)
    return {'bytes': path.stat().st_size, 'md5': md5.hexdigest(), 'sha256': sha256.hexdigest()}


def safe_link(value):
    try:
        parsed = urlparse(value)
        return parsed.scheme == 'https' and parsed.hostname == 'gofile.io' and parsed.path.startswith('/d/')
    except (TypeError, ValueError):
        return False


def validate_gofile(response, metadata):
    info = response.get('data') or {}
    if response.get('status') != 'ok':
        raise PublishError('gofile_rejected')
    if info.get('size') != metadata['bytes'] or str(info.get('md5', '')).lower() != metadata['md5']:
        raise PublishError('gofile_checksum_or_size_mismatch')
    if not safe_link(info.get('downloadPage')):
        raise PublishError('gofile_missing_download_link')
    return info['downloadPage']


def media_ok(path, timeout=15):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type',
                                 '-of', 'json', str(path)], capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0 and any(s.get('codec_type') == 'video'
                                              for s in json.loads(result.stdout).get('streams', []))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def select_media(root):
    # Include recovered nested chunks and short captures; never discard by size/name alone.
    return sorted((p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                  and p.suffix.lower() in {'.ts', '.part', '.mp4', '.mkv'}
                  and '.upload-work-' not in str(p) and p.stat().st_size > 0),
                  key=lambda p:(len(p.relative_to(root).parts),str(p)))


def split_for_release(source, destination, limit=RELEASE_LIMIT, timeout=300):
    """Keep source intact; adapt time segmentation until EVERY output fits the byte cap."""
    destination.mkdir(parents=True, exist_ok=True)
    if source.stat().st_size < limit:
        target = destination / 'part-00000.ts'
        os.link(source, target)
        return [target]
    if shutil.disk_usage(destination).free < source.stat().st_size * 1.1 + 64 * 1024 * 1024:
        raise PublishError('insufficient_disk_for_safe_split')
    started = time.monotonic()
    probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'json', str(source)], capture_output=True, text=True, timeout=min(15, timeout))
    try:
        duration = float(json.loads(probe.stdout)['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, KeyError):
        raise PublishError('split_duration_unavailable') from None
    seconds = max(.25, min(5400, duration * limit / source.stat().st_size * .65))
    for attempt in range(12):
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 1:
            raise PublishError('split_budget_exhausted')
        for old in destination.glob('part-*.ts'):
            old.unlink()
        process = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(source),
                                  '-map', '0:v:0', '-map', '0:a:0?', '-c', 'copy',
                                  '-f', 'segment', '-segment_time', str(seconds),
                                  '-reset_timestamps', '1', str(destination / 'part-%05d.ts')],
                                 capture_output=True, timeout=remaining)
        parts = sorted(destination.glob('part-*.ts'))
        if process.returncode != 0 or not parts:
            raise PublishError('split_failed_source_preserved')
        if all(0 < p.stat().st_size < limit for p in parts):
            return parts
        if seconds <= .25:
            break  # A single keyframe group may exceed even a small test limit.
        seconds = max(.25, seconds / 2)
    raise PublishError('cannot_split_below_cap_source_preserved')


class Publisher:
    def __init__(self, root, report, budget=7200, release_fallback=False, endpoint=GOFILE_ENDPOINT):
        if not 10 <= budget <= 7200:
            raise ValueError('Upload budget must be 10..7200 seconds')
        self.root, self.report = Path(root), Path(report)
        self.end = time.monotonic() + budget
        self.release_fallback, self.endpoint = release_fallback, endpoint
        self.token = os.environ.get('GOFILE_TOKEN', '')
        self.folder = os.environ.get('GOFILE_FOLDER_ID', '')
        self.drive_config = os.environ.get('RCLONE_CONFIG', '')
        self.drive_prefix = os.environ.get('GDRIVE_REMOTE', 'drive:YouTubeRecordings')
        self.results = []
        self.session_url = None
        try:self.rolling=json.loads((self.root/'rolling-backup.json').read_text())
        except (OSError,ValueError):self.rolling={}
        self.category = 'recordings'
        try:
            self.prior = json.loads(self.report.read_text()).get('files',[])
        except (OSError,ValueError,TypeError):
            self.prior = []

    def remaining(self):
        value = self.end - time.monotonic()
        if value < 1:
            raise PublishError('upload_budget_exhausted')
        return value

    def save(self):
        self.report.parent.mkdir(parents=True, exist_ok=True)
        value = {'files': self.results, 'all_uploaded': bool(self.results) and
                 all(r.get('uploaded') for r in self.results), 'session_url':self.session_url,
                 'media_status':'media_present' if self.results else 'no_media',
                 'unique_uploaded_files':sum(bool(r.get('uploaded')) and not r.get('duplicate_of') for r in self.results),
                 'safety_backup':{k:self.rolling.get(k) for k in ('state','uploaded_clips','pending_clips','last_error')}}
        temporary = self.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, indent=2))
        os.replace(temporary, self.report)

    def gofile(self, path, metadata):
        # With backup enabled, spend at most one attempt on Gofile and retain
        # at least ten minutes of the shared deadline for the backup path.
        attempts = 1 if self.release_fallback else 2
        for attempt in range(attempts):
            available = self.remaining() - (600 if self.release_fallback else 0)
            if available < 1:
                raise PublishError('gofile_budget_reserved_for_backup')
            seconds = min(600, available)
            config = ''
            if self.token:
                if any(c in self.token for c in '\r\n"\\'):
                    raise PublishError('invalid_gofile_token_binding')
                config = 'header = "Authorization: Bearer ' + self.token + '"\n'
            args = ['curl', '--config', '-', '--silent', '--show-error', '--fail',
                    '--connect-timeout', str(min(15, seconds)), '--max-time', str(seconds),
                    '--form', 'file=@' + str(path)]
            if self.folder:
                args += ['--form-string', 'folderId=' + self.folder]
            process = subprocess.run(args + [self.endpoint], input=config, capture_output=True,
                                     text=True, timeout=min(self.remaining() + 2, seconds + 2))
            if process.returncode == 0:
                try:
                    response = json.loads(process.stdout)
                    link = validate_gofile(response, metadata)
                    info = response['data']
                    self.token = info.get('guestToken') or self.token
                    self.folder = info.get('parentFolder') or self.folder
                    return {'provider': 'gofile', 'url': link, 'verification': 'provider_size_and_md5'}
                except (ValueError, TypeError, AttributeError, PublishError):
                    pass
            if attempt + 1 < attempts and self.remaining() > 6:
                time.sleep(5)
        raise PublishError('gofile_upload_failed')

    def google_drive(self, source, metadata):
        if not self.drive_config or not Path(self.drive_config).is_file():
            raise PublishError('google_drive_not_configured')
        if not shutil.which('rclone'):
            raise PublishError('rclone_missing')
        seconds = min(5400, self.remaining() - 600)
        if seconds < 1:
            raise PublishError('drive_budget_reserved_for_fallback')
        owner_repo = os.environ.get('GITHUB_REPOSITORY', 'local').replace('/', '_')
        run = os.environ.get('GITHUB_RUN_ID', 'local')
        name = re.sub(r'[^A-Za-z0-9_.-]+', '_', source.name)
        session = os.environ.get('RECORDING_SESSION_ID',run)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',session):
            raise PublishError('invalid_recording_session_id')
        window = os.environ.get('RECORDING_SHIFT_INDEX','0')
        if not window.isdigit():raise PublishError('invalid_recording_shift_index')
        session_path = self.drive_prefix.rstrip('/')+'/'+owner_repo+'/'+session
        remote = session_path+'/shift-'+window+'/'+self.category+'/'+metadata['sha256'][:20]+'-'+name
        base = ['rclone', '--config', self.drive_config]
        copied = subprocess.run(base + ['copyto', str(source), remote, '--checksum',
                                        '--retries', '2', '--low-level-retries', '3',
                                        '--contimeout', '15s', '--timeout', '60s',
                                        '--drive-chunk-size', '32M', '--transfers', '1', '--checkers', '1'],
                                capture_output=True, text=True, timeout=seconds)
        if copied.returncode:
            raise PublishError('google_drive_copy_failed')
        info = subprocess.run(base + ['lsjson', remote, '--stat', '--hash'],
                              capture_output=True, text=True, timeout=min(30, self.remaining()))
        if info.returncode:
            raise PublishError('google_drive_metadata_failed')
        details = json.loads(info.stdout)
        hashes = {k.lower(): v.lower() for k, v in details.get('Hashes', {}).items()}
        if details.get('Size') != metadata['bytes'] or hashes.get('md5') != metadata['md5']:
            raise PublishError('google_drive_checksum_or_size_mismatch')
        file_id = details.get('ID', '')
        link = 'https://drive.google.com/file/d/' + file_id + '/view' if re.fullmatch(r'[A-Za-z0-9_-]+', file_id) else remote
        return {'provider': 'google_drive', 'url': link, 'remote_path': remote,
                'session_path':session_path, 'verification': 'remote_size_and_md5', 'sharing': 'private; no sharing permissions changed'}

    def release(self, source, metadata, work):
        repo = os.environ.get('GITHUB_REPOSITORY', '')
        run = os.environ.get('GITHUB_RUN_ID', '')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or not run.isdigit():
            raise PublishError('release_environment_missing')
        tag = 'recorder-backup-' + run
        scope = os.environ.get('RELEASE_SCOPE', '')
        if scope:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,50}', scope):
                raise PublishError('invalid_release_scope')
            tag += '-' + scope
        def gh(*args):
            result = subprocess.run(['gh', *args], capture_output=True, text=True,
                                    timeout=min(300, self.remaining()))
            if result.returncode:
                status = re.search(r'HTTP (\d{3})', result.stderr)
                operation = '_'.join(args[:2]) if args[0] != 'api' else 'api'
                raise PublishError('github_release_' + operation + '_failed' +
                                   ('_http_' + status.group(1) if status else ''))
            return result.stdout
        exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo],
                                capture_output=True, timeout=min(30, self.remaining()))
        if exists.returncode:
            gh('release', 'create', tag, '--repo', repo, '--target', os.environ['GITHUB_SHA'],
               '--title', 'Recorder backup ' + run, '--notes', 'Opt-in recording backup; repository access rules apply.')
        parts = split_for_release(source, work / 'parts', timeout=min(300, self.remaining()))
        uploaded = []
        for index, part in enumerate(parts):
            if not 0 < part.stat().st_size < RELEASE_LIMIT:
                raise PublishError('release_part_over_size_cap')
            asset = work / f'{metadata["sha256"][:20]}-part-{index:05d}.ts'
            os.link(part, asset)
            gh('release', 'upload', tag, str(asset), '--repo', repo, '--clobber')
            details = json.loads(gh('release', 'view', tag, '--repo', repo, '--json', 'assets'))
            info = next((a for a in details.get('assets', []) if a.get('name') == asset.name), {})
            link = info.get('browser_download_url') or info.get('url')
            if info.get('size') != asset.stat().st_size or not link:
                raise PublishError('release_asset_size_verification_failed')
            if info.get('digest') and info['digest'] != 'sha256:' + fingerprints(asset, self.end)['sha256']:
                raise PublishError('release_asset_digest_verification_failed')
            uploaded.append({'provider': 'github_release', 'url': link,
                             'bytes': asset.stat().st_size, 'verification': 'asset_size_and_digest_if_available'})
            # Checkpoint successful parts even if a later upload fails.
            self.results[-1]['receipts'] = list(uploaded)
            self.save()
        return uploaded

    def verified_prior(self, metadata):
        # A cached success is reused only after checking the remote Drive object again.
        for prior in self.prior:
            if not isinstance(prior,dict) or not prior.get('uploaded') or prior.get('sha256')!=metadata['sha256']:
                continue
            for receipt in prior.get('receipts',[]):
                if receipt.get('provider')!='google_drive' or not self.drive_config:
                    continue
                remote=receipt.get('remote_path','')
                if not remote.startswith(self.drive_prefix.rstrip('/')+'/'):
                    continue
                try:
                    info=subprocess.run(['rclone','--config',self.drive_config,'lsjson',remote,'--stat','--hash'],
                                        capture_output=True,text=True,timeout=min(30,self.remaining()))
                    data=json.loads(info.stdout)
                    hashes={k.lower():str(v).lower() for k,v in data.get('Hashes',{}).items()}
                    if info.returncode==0 and data.get('Size')==metadata['bytes'] and hashes.get('md5')==metadata['md5']:
                        return prior
                except (OSError,ValueError,subprocess.TimeoutExpired):
                    pass
        return None

    def resolve_session_url(self):
        if not self.drive_config:return
        safety_receipts=[c.get('receipt',{}) for c in self.rolling.get('clips',[]) if c.get('status')=='uploaded']
        for record in self.results + [{'receipts':safety_receipts}]:
            for receipt in record.get('receipts',[]):
                path=receipt.get('session_path')
                if receipt.get('provider')!='google_drive' or not path:continue
                try:
                    result=subprocess.run(['rclone','--config',self.drive_config,'lsjson',path,'--stat'],
                                          capture_output=True,text=True,timeout=min(20,self.remaining()))
                    data=json.loads(result.stdout);folder=data.get('ID','')
                    if result.returncode==0 and data.get('IsDir') and re.fullmatch(r'[A-Za-z0-9_-]+',folder):
                        self.session_url='https://drive.google.com/drive/folders/'+folder
                        return
                except (OSError,ValueError,PublishError,subprocess.TimeoutExpired):pass

    def run(self):
        candidates = select_media(self.root)
        self.save()
        seen = {}
        for path in candidates:
            record = {'file': str(path.relative_to(self.root)), 'uploaded': False, 'receipts': []}
            self.results.append(record)
            self.save()
            try:
                self.remaining()
                if not media_ok(path, timeout=min(15, self.remaining())):
                    raise PublishError('unreadable_video_preserved')
                metadata = fingerprints(path, self.end)
                self.remaining()
                record.update(metadata)
                previous=seen.get(metadata['sha256'])
                if previous and previous.get('uploaded'):
                    record.update(uploaded=True,receipts=previous['receipts'],duplicate_of=previous['file'])
                    self.save()
                    print('DEDUPLICATED '+record['file'],flush=True)
                    continue
                previous=self.verified_prior(metadata)
                if previous:
                    record.update(uploaded=True,receipts=previous['receipts'],resumed=True)
                    seen[metadata['sha256']]=record
                    self.save()
                    continue
                # Safe ASCII multipart filename; hard-link avoids another multi-GB copy.
                with tempfile.TemporaryDirectory(prefix='.upload-work-', dir=path.parent) as directory:
                    work = Path(directory)
                    staged = work / (metadata['sha256'][:20] + '.ts')
                    os.link(path, staged)
                    receipts = None
                    if self.drive_config:
                        try:
                            receipts = [self.google_drive(path, metadata)]
                        except (PublishError, OSError, ValueError, subprocess.TimeoutExpired):
                            record['drive_attempt_failed'] = True
                            self.save()
                    if receipts is None:
                        try:
                            receipts = [self.gofile(staged, metadata)]
                        except (PublishError, subprocess.TimeoutExpired):
                            if not self.release_fallback:
                                raise PublishError('gofile_failed_source_preserved') from None
                            receipts = self.release(path, metadata, work)
                    record.update(uploaded=True, receipts=receipts)
                    seen[metadata["sha256"]]=record
                    self.save()
                # Keep source on disk even after upload; lifecycle cleanup is explicit.
                print('UPLOADED ' + record['file'], flush=True)
            except (PublishError, OSError, ValueError, subprocess.TimeoutExpired) as error:
                record['error'] = str(error) if isinstance(error, PublishError) else type(error).__name__
                print('UPLOAD_FAILED ' + record['file'] + ': ' + record['error'], flush=True)
            self.save()
        self.resolve_session_url()
        self.save()
        if not candidates:
            print('NO_RECORDING: no media files; this is not capture success', flush=True)
        return 0 if self.results and all(r['uploaded'] for r in self.results) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='streams')
    parser.add_argument('--report', default='upload-report.json')
    parser.add_argument('--budget', type=int, default=7200)
    parser.add_argument('--release-fallback', action='store_true')
    args = parser.parse_args()
    raise SystemExit(Publisher(args.root, args.report, args.budget, args.release_fallback).run())


if __name__ == '__main__':
    main()
