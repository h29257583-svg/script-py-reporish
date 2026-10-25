"""Run the recorder and optional Drive safety copies; retain the recorder's exit status."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading

from rolling_backup import SafetyArchive


def main():
    root=Path(os.environ.get('OUT_DIR','streams'));root.mkdir(parents=True,exist_ok=True)
    value=os.environ.pop('RCLONE_CONFIG_TEXT','')
    done=threading.Event()
    with tempfile.TemporaryDirectory(prefix='private-capture-config-') as directory:
        config=None
        if value:
            config=Path(directory)/'rclone.conf';config.write_text(value);config.chmod(0o600);value=''
        archive=SafetyArchive(root,str(config) if config else os.environ.get('RCLONE_CONFIG'),
                              seconds=float(os.environ.get('SAFETY_CLIP_SECONDS','300')),
                              overlap=float(os.environ.get('SAFETY_CLIP_OVERLAP','15')))
        worker=threading.Thread(target=archive.run,args=(done,),daemon=True);worker.start()
        child_env=dict(os.environ)
        for key in ('GH_TOKEN','GITHUB_TOKEN','GOFILE_TOKEN','TELEGRAM_BOT_TOKEN','TELEGRAM_CHAT_ID'):
            child_env.pop(key,None)
        child=subprocess.Popen([sys.executable,'recorder.py'],env=child_env)
        def stop(signum,frame):
            if child.poll() is None:child.send_signal(signal.SIGINT)
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try:returncode=child.wait()
        finally:
            done.set();worker.join(timeout=360)
            archive.stop.set()
        return returncode


if __name__=='__main__':raise SystemExit(main())
