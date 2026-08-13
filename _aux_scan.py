import re, subprocess, os

TRACKED = set(subprocess.check_output(['git','ls-files'], text=True).splitlines())

def tracked(p):
    return 'TRACKED' if p in TRACKED else 'UNTRACKED'

# key -> (SECRET_TYPE, regex on the line containing a distinguishing marker without printing value)
patterns = [
  ('MT5_PASSWORD / MQ5 password', re.compile(r'(?i)(mt5_password|password\s*[:=]|pass\s*[:=])')),
  ('MT5_LOGIN', re.compile(r'(?i)(mt5_login|login\s*[:=])')),
  ('MT5_SERVER', re.compile(r'(?i)(mt5_server|server\s*[:=]|MetaQuotes)')),
  ('OPENAI_API_KEY', re.compile(r'(?i)(openai|sk-[a-zA-Z0-9])')),
  ('ANTHROPIC_API_KEY', re.compile(r'(?i)(anthropic|sk-ant-)')),
  ('TELEGRAM_BOT_TOKEN', re.compile(r'(?i)(telegram_bot_token|bot_token|\d+:AA)')),
  ('TELEGRAM_CHAT_ID', re.compile(r'(?i)(telegram_chat_id|chat_id)')),
  ('DISCORD_WEBHOOK', re.compile(r'(?i)(discord_webhook|discord.*token)')),
  ('SMTP_PASSWORD', re.compile(r'(?i)(smtp_password|smtp.*pass)')),
  ('GENERIC_TOKEN/API_KEY', re.compile(r'(?i)(api_key|access_token|auth_token|secret\s*[:=]|credentials)')),
  ('PRIVATE_KEY/CERT', re.compile(r'(?i)(-----BEGIN|private_key|rsa\.key|\.pem)')),
]

# files/dirs to scan (tracked -> from git; also on-disk sensitive even if untracked)
files = set(TRACKED)
files.update(['.env', 'run_out.log', 'run_err.log'])
# add scripts dir
for root,_,fs in os.walk('scripts'):
    for f in fs:
        files.add(os.path.join(root,f))

# only inspect text-ish files
skip_ext=('.png','.jpg','.pyc','.so','.csv','.pdf')
for path in sorted(files):
    if not os.path.isfile(path):
        continue
    if any(path.endswith(e) for e in skip_ext):
        continue
    try:
        with open(path,'r',encoding='utf-8',errors='replace') as fh:
            lines=fh.readlines()
    except Exception as e:
        continue
    for i,line in enumerate(lines,1):
        for st_type, rx in patterns:
            m=rx.search(line)
            if m:
                print(f'{st_type}\t{path}:{i}\t{tracked(path)}')
                break
