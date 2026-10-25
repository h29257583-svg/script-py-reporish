"""Bounded repository-specific validation. Secret values never enter reports."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from publish_recordings import Publisher, PublishError


def main():
    report={'repository':os.environ.get('GITHUB_REPOSITORY','local'),'scope':'Bounded development validation, not an overnight reliability guarantee','stages':{}}
    def save():Path('daily-validation.json').write_text(json.dumps(report,indent=2))
    def command(stage,args,env=None,timeout=180):
        result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=timeout)
        report['stages'][stage]={'status':'PASS' if result.returncode==0 else 'FAIL','exit_code':result.returncode}
        if stage=='live_capture':
            from hls_capture import MESSAGES
            events=[]
            for line in result.stdout.splitlines():
                for reason,message in MESSAGES.items():
                    if 'ERROR: '+message in line:events.append(reason)
            report['stages'][stage]['transport_error_codes']=events[-20:]
        save();return result.returncode==0
    try:
        from check_telegram import main as telegram_readiness
        report['optional_telegram']=telegram_readiness()
        if os.environ.get('RUN_UNIT_TESTS')=='true':
            if not command('regression_tests',[sys.executable,'-m','unittest','test_reliability','test_retry_backoff','test_daily_reliability','test_private_setup_helper','-q'],timeout=180):return 1
        if os.environ.get('RUN_FAULT_TEST')=='true':
            command('network_blink',[sys.executable,'test_network_blink.py'],timeout=150)
            path=Path('validation/network-blink-streamlink/result.json')
            if path.exists():report['stages']['network_blink']['evidence']=json.loads(path.read_text())
        if not command('secret_format',[sys.executable,'check_setup.py']):return 1
        if not command('drive_readback',[sys.executable,'test_drive_connection.py']):return 1
        if os.environ.get('CAPTURE_LIVE')!='true':return 0
        duration=int(os.environ.get('VALIDATION_SECONDS','90'))
        if not 30<=duration<=180:raise ValueError('validation_seconds_must_be_30_to_180')
        handle=os.environ.get('VALIDATION_CHANNEL','@SkyNews')
        if not re.fullmatch(r'@[A-Za-z0-9_.-]{2,80}',handle):raise ValueError('invalid_validation_channel')
        with tempfile.TemporaryDirectory(prefix='private-live-validation-') as d:
            cookies=Path(d)/'cookies.txt';cookies.write_text(os.environ.pop('YT_COOKIES',''));cookies.chmod(0o600)
            config=Path(d)/'rclone.conf';config.write_text(os.environ.pop('RCLONE_CONFIG_TEXT',''));config.chmod(0o600)
            os.environ['RCLONE_CONFIG']=str(config)
            env=dict(os.environ,CHANNEL_HANDLE=handle,TARGET_CHANNEL_ID='',HOURS=str(duration/3600),
                     SESSION_DEADLINE=str(time.time()+duration),OUT_DIR='streams',USE_COOKIES='true',COOKIE_FILE=str(cookies),
                     SAFETY_CLIP_SECONDS='20',SAFETY_CLIP_OVERLAP='2',SKIP_DEPENDENCY_INSTALL='true',
                     RECORDING_SESSION_ID=os.environ.get('GITHUB_RUN_ID','local-validation'),RECORDING_SHIFT_INDEX='0')
            os.environ['RECORDING_SESSION_ID']=env['RECORDING_SESSION_ID'];os.environ['RECORDING_SHIFT_INDEX']='0'
            command('live_capture',[sys.executable,'capture_session.py'],env=env,timeout=duration+300)
            files=sorted(Path('streams').glob('*.ts'))
            if not files:files=sorted(Path('streams/_chunks').rglob('*.ts'))
            decode=[]
            for path in files:
                result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','null','-'],capture_output=True,timeout=90)
                probe=subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_type,width,height:format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=20)
                data=json.loads(probe.stdout) if probe.returncode==0 else {}
                decode.append({'file':str(path),'full_decode_ok':result.returncode==0 and not result.stderr,'metadata':data})
            report['stages']['full_decode']={'status':'PASS' if decode and all(x['full_decode_ok'] for x in decode) else 'FAIL','files':decode}
            publisher=Publisher('streams','upload-report.json',budget=900,release_fallback=False)
            def drive_only(*args):raise PublishError('validation_requires_drive')
            publisher.gofile=drive_only
            uploaded=publisher.run()
            report['stages']['video_drive_upload']={'status':'PASS' if uploaded==0 else 'FAIL'}
            upload=json.loads(Path('upload-report.json').read_text());report['session_url']=upload.get('session_url')
            report['files_uploaded']=upload.get('unique_uploaded_files')
            report['stages']['session_folder']={'status':'PASS' if upload.get('session_url') else 'FAIL'}
            for summary in Path('streams').glob('*_summary.json'):
                report['capture_summary']=json.loads(summary.read_text())
            if Path('streams/rolling-backup.json').exists():report['safety_backup']=json.loads(Path('streams/rolling-backup.json').read_text())
            save()
        return 0 if all(stage.get('status')=='PASS' for stage in report['stages'].values()) else 1
    except Exception as error:
        report['failure_type']=type(error).__name__;return 1
    finally:
        report['status']='PASS' if report['stages'] and not report.get('failure_type') and all(s.get('status')=='PASS' for s in report['stages'].values()) else 'FAIL'
        save();print(json.dumps({'repository':report['repository'],'status':report['status'],'stages':{k:v['status'] for k,v in report['stages'].items()}}))

if __name__=='__main__':raise SystemExit(main())
