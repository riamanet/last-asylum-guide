#!/usr/bin/env python3
"""Automatically publish codes only from verified Discord announcement crossposts.

The bot is invited only to the owner's collection server, never to the official
server. Human reports remain pending approval in the separate Sheets backend.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import time
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parents[1]
CODES_PATH = ROOT / 'codes.json'
STATE_PATH = ROOT / 'coupon-state.json'
LOG_PATH = ROOT / 'coupon-publication-log.json'
CHANNEL_ID = os.environ.get('DISCORD_CHANNEL_ID', '1561596575501910120').strip()
TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '').strip()
SOURCE_OVERRIDE = os.environ.get('DISCORD_SOURCE_CHANNEL_ID', '').strip()
API = 'https://discord.com/api/v10'
IS_CROSSPOST = 1 << 1  # Official Discord message flag
CHANNEL_FOLLOW_ADD = 12

STOP_WORDS = {
    'DISCORD', 'EXCLUSIVE', 'REWARDS', 'GIFTCODE', 'GIFT', 'CODES',
    'COUPON', 'FOLLOW', 'MERRY', 'CHRISTMAS', 'UPDATE', 'EVENT',
    'OFFICIAL', 'REDEEM', 'THANKYOU', 'WELCOME', 'BONUS', 'PLAYER',
    'GUIDE', 'ANNOUNCEMENT', 'REWARD', 'GIFTCODES', 'COUPONS',
}
TOKEN_LINE = re.compile(r'^[\s`*#>\-:]*([A-Za-z0-9]{5,20})[\s`*!.,:]*$')
LABELED = re.compile(r'(?i)(?:gift\s*codes?|redeem\s*codes?|coupon\s*codes?|codes?|쿠폰\s*코드|기프트\s*코드)\s*[:：=\-]\s*[`*\s]*([A-Za-z0-9]{5,20})')


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict) -> None:
    value = json.dumps(payload, ensure_ascii=False, indent=2) + '\n'
    if not path.exists() or path.read_text(encoding='utf-8') != value:
        path.write_text(value, encoding='utf-8')


def normalize(code: str) -> str:
    return str(code).strip().upper()


def likely_code(code: str) -> bool:
    candidate = normalize(code)
    if candidate in STOP_WORDS or not re.fullmatch(r'[A-Z0-9]{5,20}', candidate):
        return False
    return (any(ch.isalpha() for ch in candidate) and any(ch.isdigit() for ch in candidate)) or (len(candidate) >= 6 and code.isupper())


def extract_codes(message: dict) -> list[str]:
    texts = [message.get('content') or '']
    for embed in message.get('embeds') or []:
        texts.extend([embed.get('title') or '', embed.get('description') or ''])
        for field in embed.get('fields') or []:
            texts.extend([field.get('name') or '', field.get('value') or ''])
    found = set()
    for txt in texts:
        for match in LABELED.finditer(txt):
            if likely_code(match.group(1)):
                found.add(normalize(match.group(1)))
        for line in txt.splitlines():
            match = TOKEN_LINE.fullmatch(line.strip())
            if match and likely_code(match.group(1)):
                found.add(normalize(match.group(1)))
    return sorted(found)


def official_source_id(message: dict) -> str:
    """Source announcement channel from a real Discord crosspost reference."""
    if not (int(message.get('flags') or 0) & IS_CROSSPOST):
        return ''
    ref = message.get('message_reference') or {}
    source = str(ref.get('channel_id') or '')
    return source if re.fullmatch(r'\d{17,22}', source) else ''


def infer_source_id(messages: list[dict], state: dict) -> str:
    if SOURCE_OVERRIDE:
        if not re.fullmatch(r'\d{17,22}', SOURCE_OVERRIDE):
            raise RuntimeError('DISCORD_SOURCE_CHANNEL_ID 값은 17~22자리 숫자여야 합니다.')
        return SOURCE_OVERRIDE
    existing = str(state.get('official_source_channel_id') or '')
    if existing:
        return existing
    # When an announcement channel is followed, Discord posts a type-12
    # follow event with a message_reference pointing to the source channel.
    followed_sources = {
        str((msg.get('message_reference') or {}).get('channel_id'))
        for msg in messages if msg.get('type') == CHANNEL_FOLLOW_ADD
        and (msg.get('message_reference') or {}).get('channel_id')
    }
    if len(followed_sources) == 1:
        return next(iter(followed_sources))
    if len(followed_sources) > 1:
        print('여러 공지 채널이 팔로우되어 있어 출처가 불분명합니다. DISCORD_SOURCE_CHANNEL_ID를 설정해주세요.')
    else:
        print('공식 채널 ID 자동 확인 대기 중: 팔로우 안내 메시지를 찾지 못했어요. DISCORD_SOURCE_CHANNEL_ID를 설정할 수도 있습니다.')
    return ''


def api_get(path: str, params: dict | None = None):
    url = API + path
    if params:
        url += '?' + parse.urlencode(params)
    for attempt in range(4):
        req = request.Request(url, headers={
            'Authorization': 'Bot ' + TOKEN,
            'User-Agent': 'LastAsylumGiftCodeCollector/2.0',
            'Accept': 'application/json',
        })
        try:
            with request.urlopen(req, timeout=22) as response:
                return json.load(response)
        except error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                try:
                    wait = float(json.loads(exc.read().decode('utf-8')).get('retry_after', 2))
                except (ValueError, TypeError):
                    wait = 2
                time.sleep(min(wait, 15))
                continue
            if exc.code in (401, 403):
                raise RuntimeError('Discord 접근 실패: 봇 토큰과 채널 보기·메시지 기록 보기 권한 확인') from None
            raise RuntimeError(f'Discord API 요청 실패: HTTP {exc.code}') from None
    raise RuntimeError('Discord 요청 제한이 지속됩니다. 다음 실행에 재시도하세요.')


def get_unseen_messages(last_id: int) -> list[dict]:
    result = []
    before = None
    for _ in range(10):
        opts = {'limit': 100}
        if before:
            opts['before'] = before
        batch = api_get(f'/channels/{CHANNEL_ID}/messages', opts)
        if not batch:
            break
        new = [msg for msg in batch if int(msg['id']) > last_id]
        result.extend(new)
        if len(new) < len(batch) or len(batch) < 100:
            break
        before = batch[-1]['id']
    return sorted(result, key=lambda x: int(x['id']))


def collect(messages: list[dict], codes: dict, state: dict, log: dict, guild_id: str) -> int:
    source_id = infer_source_id(messages, state)
    if not source_id:
        # Retry all messages in a future run when official source can be verified.
        return 0
    state['official_source_channel_id'] = source_id
    known = {normalize(c) for c in codes.get('codes', [])}
    blocked = {normalize(c) for c in codes.get('blocked', [])}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    additions = []
    for msg in messages:
        # Regular users and ordinary webhooks cannot automatically publish codes.
        if official_source_id(msg) != source_id:
            continue
        ref = msg.get('message_reference') or {}
        source_url = f"https://discord.com/channels/{ref.get('guild_id')}/{source_id}/{ref.get('message_id')}" if ref.get('guild_id') and ref.get('message_id') else f"https://discord.com/channels/{guild_id}/{CHANNEL_ID}/{msg['id']}"
        for code in extract_codes(msg):
            if code in known or code in blocked:
                continue
            known.add(code)
            additions.append(code)
            log.setdefault('items', []).append({'code': code, 'source': source_url, 'found_at': now, 'source_type': 'official_discord_crosspost'})
    if additions:
        # Newest entries first, older entries stay in their previous order.
        codes['codes'] = list(reversed(additions)) + codes.get('codes', [])
        codes['updated'] = dt.datetime.now(dt.timezone.utc).date().isoformat()
        log['items'] = log['items'][-500:]
    if messages:
        state['last_message_id'] = str(max(int(m['id']) for m in messages))
    return len(additions)


def main():
    if not TOKEN:
        raise RuntimeError('GitHub 비밀 DISCORD_BOT_TOKEN을 설정해주세요.')
    if not re.fullmatch(r'\d{17,22}', CHANNEL_ID):
        raise RuntimeError('DISCORD_CHANNEL_ID 형식 오류')
    codes = read_json(CODES_PATH, {'codes': [], 'blocked': [], 'updated': ''})
    state = read_json(STATE_PATH, {'last_message_id': '0'})
    log = read_json(LOG_PATH, {'items': []})
    channel = api_get(f'/channels/{CHANNEL_ID}')
    messages = get_unseen_messages(int(state.get('last_message_id', '0')))
    count = collect(messages, codes, state, log, str(channel.get('guild_id', '')))
    write_json(CODES_PATH, codes)
    write_json(STATE_PATH, state)
    write_json(LOG_PATH, log)
    print(f'새 메시지 {len(messages)}개 중 공식 공지 신규 코드 {count}개 자동 공개')
    print('이용자 직접 제보는 Google Sheets 승인 대기에서 별도 처리됩니다.')


if __name__ == '__main__':
    main()
