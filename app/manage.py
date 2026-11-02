"""Local-only credential and launch commands. Secrets never appear in CLI arguments."""
import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .storage import DB, ROOT, Vault


def main():
    if sys.stdout is not None and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser()
    managed = ['start','stop','restart','status','diagnostics','check-ai','test-ai','test-item-query','test-weekly-query','test-pmix-query','test-forecast-query','test-build-query','test-ai-telegram','test-briefing','check-outlook','check-telegram','test-telegram']
    parser.add_argument('command', choices=['serve', 'configure', 'import-monday', 'check-toast', 'backup', 'reset-password', 'test-ezcater', 'setup-code','connect-outlook','login-chatgpt']+managed)
    parser.add_argument('--file', help='Local monday.com Excel export for import-monday')
    parser.add_argument('--board-url', default='')
    args = parser.parse_args()
    directory = Path(os.getenv('TONY_DATA_DIR', ROOT / 'data'))
    vault, db = Vault(directory), DB(directory)
    if args.command in managed:
        from .lifecycle import execute
        import httpx
        try:
            print(json.dumps(execute(db,args.command),indent=2))
        except RuntimeError as error:
            raise SystemExit(str(error)) from None
        except httpx.HTTPError:
            raise SystemExit('Management request did not complete. Use status; check data/app.log. Start the app with start if stopped.') from None
    elif args.command == 'import-monday':
        from .monday import Monday
        if not args.file or Path(args.file).suffix.lower() != '.xlsx':
            raise SystemExit('Provide --file with a monday.com .xlsx board export.')
        result = Monday(vault, db).import_export(Path(args.file).read_bytes(), args.board_url)
        print(json.dumps(result, indent=2))
    elif args.command == 'login-chatgpt':
        import webbrowser
        webbrowser.open('http://127.0.0.1:'+os.getenv('TONY_PORT','8790')+'/#settings')
        print('In the dashboard, sign in locally then select Continue with ChatGPT. To use existing ChatGPT credits after included usage, set this app limit to 100% and enable app credit use in ChatGPT Settings > Usage. Review automatic credit purchases separately.')
    elif args.command == 'connect-outlook':
        from .outlook import Outlook, OutlookError
        provider=Outlook(vault,db)
        try:
            print('See OUTLOOK-SETUP.md to register a public client with delegated Mail.Read only.')
            client=input('Application (client) ID: ').strip()
            tenant=input('Directory (tenant) ID: ').strip()
            print(json.dumps(provider.connect(client,tenant),indent=2))
        except Exception as e:
            raise SystemExit(str(e) if isinstance(e,OutlookError) else 'Microsoft sign-in could not complete. Check the registration and network; no credentials were printed.') from None
        finally:
            asyncio.run(provider.http.aclose())
    elif args.command == 'setup-code':
        print(vault.read().get('setup_code') or 'Setup is complete. Use your chosen dashboard password.')
    elif args.command == 'configure':
        choice = input('Configure toast or telegram: ').strip().lower()
        if choice == 'toast':
            vault.update(toast={'client_id': getpass.getpass('Toast client ID: '), 'client_secret': getpass.getpass('Toast client secret: '),
                                'restaurant_guid': getpass.getpass('Restaurant GUID: '), 'host': input('API host [https://ws-api.toasttab.com]: ').strip() or 'https://ws-api.toasttab.com'})
        elif choice == 'telegram':
            token = getpass.getpass('BotFather token: ')
            user = input('Your numeric Telegram user ID: ').strip()
            chat = input('Your numeric PRIVATE chat ID (usually same as user ID): ').strip()
            if not user.isdigit() or not chat.isdigit() or not token:
                raise SystemExit('A token and positive numeric IDs are required. Group chats are not supported.')
            vault.update(telegram={'bot_token': token, 'user_id': user, 'chat_id': chat, 'enabled': True})
        else:
            raise SystemExit('Choose toast or telegram.')
        print('Saved in Windows DPAPI vault. Restart the application to refresh provider state.')
    elif args.command == 'check-toast':
        from .providers import Toast
        async def check():
            provider = Toast(vault)
            try:
                cfg = provider.config()
                r = await provider.get('/restaurants/v1/restaurants/' + cfg['restaurant_guid'])
                print(json.dumps({'connected': True, 'restaurant_name': r.get('general', {}).get('name'), 'closeout_hour': r.get('general', {}).get('closeoutHour')}))
            finally:
                await provider.http.aclose()
        asyncio.run(check())
    elif args.command == 'backup':
        print(db.backup())
    elif args.command == 'reset-password':
        from .main import hash_password
        password = getpass.getpass('New dashboard password (at least 12 characters): ')
        if len(password) < 12:
            raise SystemExit('Password too short.')
        vault.update(password_hash=hash_password(password), setup_code='')
        db.execute('DELETE FROM sessions')
        print('Password updated; prior sessions ended.')
    elif args.command == 'test-ezcater':
        import hashlib, hmac, time
        from .providers import validate_ezcater
        body = (ROOT / 'tests' / 'fixtures' / 'ezcater-example.json').read_bytes()
        stamp = str(int(time.time()))
        signature = stamp + '.' + hmac.new(b'test-secret', stamp.encode()+b'.'+body, hashlib.sha256).hexdigest()
        print(json.dumps({'test_mode': True, 'signature_valid': validate_ezcater(body, signature, 'test-secret'), 'external_requests': 0}))
    else:
        import uvicorn
        from .main import create_app
        from .server_lock import acquire_server_lock
        port = int(os.getenv('TONY_PORT', '8790'))
        lock = acquire_server_lock(directory / 'server.lock')
        if lock is None:
            print(f'TonyRestaurantOps is already running. Open http://127.0.0.1:{port} in your browser.')
            return
        app = create_app(directory)
        print(f"the restaurant Operations: http://127.0.0.1:{port}")
        # Access logs disabled: OAuth callback query strings must never be logged.
        log_config = {'version': 1, 'disable_existing_loggers': False,
                      'formatters': {'safe': {'format': '%(asctime)s %(levelname)s %(message)s'}},
                      'handlers': {'file': {'class': 'logging.handlers.RotatingFileHandler', 'filename': str(directory/'app.log'),
                                            'maxBytes': 1048576, 'backupCount': 3, 'encoding': 'utf-8', 'formatter': 'safe'}},
                      'root': {'handlers': ['file'], 'level': 'WARNING'},
                      'loggers': {'uvicorn': {'handlers': ['file'], 'level': 'INFO', 'propagate': False},
                                  'httpx': {'level': 'CRITICAL'}, 'httpcore': {'level': 'CRITICAL'}}}
        server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,proxy_headers=False,log_config=log_config))
        app.state.shutdown = lambda: setattr(server,'should_exit',True)
        runtime={'pid':os.getpid(),'started_at':app.state.started_at,'port':port}
        statefile=directory/'runtime.json'
        statefile.write_text(json.dumps(runtime),encoding='utf-8')
        try:
            server.run()
        finally:
            statefile.unlink(missing_ok=True)
            lock.close()


if __name__ == '__main__':
    main()
