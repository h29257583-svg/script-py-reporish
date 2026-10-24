"""One private generated-file upload/readback; never publishes tokens or file contents."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from publish_recordings import Publisher, PublishError, fingerprints


def main():
    result = {'status': 'FAIL', 'scope': 'Small generated-file Google Drive connection test, not multi-GB/overnight validation',
              'remote_size_md5_confirmed': False, 'readback_sha256_matches': False, 'cleanup_success': False}
    with tempfile.TemporaryDirectory(prefix='drive-check-private-') as directory:
        root = Path(directory)
        value = os.environ.pop('RCLONE_CONFIG_TEXT', '')
        config = root/'rclone.conf'
        remote = None
        try:
            if not value:
                raise RuntimeError('missing_RCLONE_CONFIG_secret')
            parsed = configparser.ConfigParser(interpolation=None)
            parsed.read_string(value)
            if parsed.get('drive','type',fallback='') != 'drive' or not parsed.get('drive','client_id',fallback='') or not parsed.get('drive','client_secret',fallback=''):
                raise RuntimeError('own_Google_OAuth_client_not_configured')
            config.write_text(value); config.chmod(0o600); value = ''
            os.environ['RCLONE_CONFIG'] = str(config)
            base = ['rclone','--config',str(config)]
            about = subprocess.run(base+['about','drive:','--json'],capture_output=True,text=True,timeout=45)
            if about.returncode:
                raise RuntimeError('drive_quota_or_authorization_check_failed')
            quota = json.loads(about.stdout)
            result['quota_bytes'] = {k:quota[k] for k in ('total','used','free') if isinstance(quota.get(k),int)}
            source = root/'drive-connection-test.txt'
            source.write_bytes(b'Owned GitHub recorder Google Drive connection test.\n' * 40)
            receipt = Publisher(root, root/'unused.json', budget=780).google_drive(source,fingerprints(source))
            remote = receipt['remote_path']
            result['remote_size_md5_confirmed'] = True
            returned = subprocess.run(base+['cat',remote],capture_output=True,timeout=45)
            if returned.returncode or hashlib.sha256(returned.stdout).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise RuntimeError('drive_readback_checksum_failed')
            result.update(status='PASS',readback_sha256_matches=True,bytes=source.stat().st_size,
                          sharing='Private; no public link/share permission created')
        except Exception as error:
            result['failure'] = str(error) if isinstance(error,(RuntimeError,PublishError)) else type(error).__name__
        finally:
            value = ''
            if remote and config.exists():
                cleanup = subprocess.run(['rclone','--config',str(config),'deletefile',remote],capture_output=True,timeout=45)
                result['cleanup_success'] = cleanup.returncode == 0
                if cleanup.returncode:
                    result['status'] = 'FAIL'
                    result['failure'] = 'test_file_cleanup_failed'
            Path('drive-test-result.json').write_text(json.dumps(result,indent=2))
            print(json.dumps(result))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
