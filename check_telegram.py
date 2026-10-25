"""Read-only Telegram configuration check. Never sends a message or logs a token."""
import json,os,re,urllib.error,urllib.parse,urllib.request
from pathlib import Path


def check(token,chat,opener=urllib.request.urlopen):
    report={'read_only':True,'message_send_tested':False,'configured':bool(token and chat)}
    if not token or not chat:return dict(report,status='NOT_CONFIGURED')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{25,}',token) or not re.fullmatch(r'-?\d+|@[A-Za-z0-9_]+',chat):
        return dict(report,status='INVALID_BINDING')
    try:
        for method,data in [('getMe',{}),('getChat',{'chat_id':chat})]:
            request=urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,data=urllib.parse.urlencode(data).encode())
            with opener(request,timeout=15) as response:
                if not json.load(response).get('ok'):return dict(report,status='API_REJECTED',failed_method=method)
        return dict(report,status='READ_ACCESS_VERIFIED')
    except urllib.error.HTTPError as error:return dict(report,status='HTTP_ERROR',http_status=error.code)
    except (urllib.error.URLError,TimeoutError,ValueError):return dict(report,status='CONNECTION_OR_RESPONSE_ERROR')


def main():
    result=check(os.environ.pop('TELEGRAM_BOT_TOKEN',''),os.environ.pop('TELEGRAM_CHAT_ID',''))
    Path('telegram-readiness.json').write_text(json.dumps(result,indent=2));print(json.dumps(result));return result
if __name__=='__main__':main()
