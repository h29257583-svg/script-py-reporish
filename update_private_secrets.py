"""Run on your own PC with GitHub CLI. Read local files; never print credential values."""
import argparse
import configparser
import json
import getpass
import re
from pathlib import Path
import shutil
import subprocess
import sys

REPOS={
 'gauravdev833844-gif':['py-task-runner','py-task-reposit','py-task-newrepo','py-task-repo4','yt-recorder'],
 'h29257583-svg':['py-task-floxrepo','py-task-reposaksh','py-task-pbrepo','py-task-repouzii','script-py-reporish'],
 'johnwatson739418-lab':['python-script-task','py-task-repo-pran','py-task-repoansh','py-task-repoarhan','py-task-reponihaan','python-task-repogoredo']}


def read_drive(path):
    value=Path(path).read_text(encoding='utf-8-sig')
    conf=configparser.ConfigParser(interpolation=None);conf.read_string(value)
    token=json.loads(conf.get('drive','token',fallback='{}'))
    if conf.get('drive','type',fallback='')!='drive' or not token.get('refresh_token') or not conf.get('drive','client_id',fallback='') or not conf.get('drive','client_secret',fallback=''):
        raise ValueError('Config must have a drive remote, your OAuth client and a refresh token')
    return value.encode()


def read_cookies(path):
    value=Path(path).read_text(encoding='utf-8-sig')
    valid=False
    for line in value.splitlines():
        if line.startswith('#HttpOnly_'):line=line[len('#HttpOnly_'):]
        if not line or line.startswith('#'):continue
        fields=line.split('\t')
        if len(fields)>=7 and (fields[0].lstrip('.').lower()=='youtube.com' or fields[0].lstrip('.').lower().endswith('.youtube.com')):valid=True
    if not valid:raise ValueError('Cookie file is not a Netscape YouTube cookie file')
    return value.encode()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--owner',required=True,choices=REPOS)
    p.add_argument('--drive-config',required=True);p.add_argument('--cookies-path');p.add_argument('--set-telegram',action='store_true');p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    if not shutil.which('gh'):raise SystemExit('Install GitHub CLI from https://cli.github.com/ first.')
    data={'RCLONE_CONFIG':read_drive(args.drive_config)}
    if args.cookies_path:data['YT_COOKIES']=read_cookies(args.cookies_path)
    if args.set_telegram:
        token=getpass.getpass('Telegram bot token (hidden): ').strip()
        chat=getpass.getpass('Telegram chat ID (hidden): ').strip()
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{25,}',token) or not re.fullmatch(r'-?\d+|@[A-Za-z0-9_]+',chat):raise ValueError('Invalid Telegram binding')
        data.update(TELEGRAM_BOT_TOKEN=token.encode(),TELEGRAM_CHAT_ID=chat.encode())
    result=subprocess.run(['gh','api','user','--jq','.login'],capture_output=True,text=True)
    if result.returncode or result.stdout.strip()!=args.owner:
        raise SystemExit('GitHub CLI must be logged in as '+args.owner+'. Use gh auth login or gh auth switch; do not paste tokens into chat.')
    failed=[]
    for name in REPOS[args.owner]:
        repo=args.owner+'/'+name
        for secret,value in data.items():
            if args.dry_run:print(repo+': would update '+secret);continue
            result=subprocess.run(['gh','secret','set',secret,'--repo',repo],input=value,capture_output=True)
            print(repo+': '+secret+' '+('UPDATED' if result.returncode==0 else 'FAILED'))
            if result.returncode:failed.append(repo+'/'+secret)
    data.clear()
    if failed:raise SystemExit('Some updates failed. Check owner access/GitHub CLI permissions; raw errors withheld.')
    print('Dry-run complete; no secrets changed.' if args.dry_run else 'Updates complete. No secret values were printed or saved to another file.')

if __name__=='__main__':
    try:main()
    except (OSError,ValueError,configparser.Error):raise SystemExit('Local file missing or config format invalid; no secret content shown.') from None
