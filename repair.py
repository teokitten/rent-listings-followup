import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, 'data', 'listings.json')
SKIPPED_FILE = os.path.join(BASE_DIR, 'backups', 'listings_repair_skipped.txt')

LISTING_START = re.compile(r'\{\s*"id":')
WHATSAPP_HEADER = re.compile(r'\[\d+/\d+')
SENTENCE_END = re.compile(r'\.(?=\s|$)|\n')


def extract_listings(raw):
    decoder = json.JSONDecoder()
    listings, skipped = [], []
    pos = 0
    while True:
        m = LISTING_START.search(raw, pos)
        if not m:
            break
        try:
            obj, end = decoder.raw_decode(raw, m.start())
            listings.append(obj)
            pos = end
        except json.JSONDecodeError:
            nxt = LISTING_START.search(raw, m.start() + 1)
            skipped.append(raw[m.start(): nxt.start() if nxt else len(raw)])
            pos = m.start() + 1
    return listings, skipped


def created_ts(listing):
    logs = listing.get('log') or []
    for e in logs:
        if e.get('type') == 'created':
            return e.get('timestamp') or ''
    return max((e.get('timestamp') or '' for e in logs), default='')


def comment_rank(comment):
    comment = comment or ''
    dirty = 'https://' in comment or bool(WHATSAPP_HEADER.search(comment))
    return (comment == '', dirty, len(comment))


def merge_duplicates(listings):
    groups = {}
    for listing in listings:
        groups.setdefault(listing.get('url'), []).append(listing)

    merged, merged_count = [], 0
    for group in groups.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        merged_count += len(group) - 1
        group.sort(key=created_ts)
        result = dict(group[-1])
        for older in group[:-1]:
            for key, value in older.items():
                if key not in result or result[key] in (None, '', []):
                    result[key] = value
        seen, logs = set(), []
        for listing in group:
            for e in listing.get('log') or []:
                key = (e.get('timestamp'), e.get('type'), e.get('content'))
                if key not in seen:
                    seen.add(key)
                    logs.append(e)
        logs.sort(key=lambda e: e.get('timestamp') or '')
        result['log'] = logs
        result['comments'] = min((l.get('comments') or '' for l in group), key=comment_rank)
        merged.append(result)
    return merged, merged_count


def is_bad_log_entry(entry):
    content = entry.get('content')
    if not isinstance(content, str):
        return False
    return ('[9/' in content or '[10/' in content
            or len(content) > 500 or content.count('https://') > 1)


def clean_logs(listings):
    removed = 0
    for listing in listings:
        kept = []
        for e in listing.get('log') or []:
            if is_bad_log_entry(e):
                removed += 1
            else:
                kept.append(e)
        listing['log'] = kept
    return removed


def truncate_comments(listings):
    truncated = 0
    for listing in listings:
        comment = listing.get('comments') or ''
        if comment.count('https://') > 1 or len(comment) > 400:
            m = SENTENCE_END.search(comment)
            listing['comments'] = comment[:m.start()].strip() if m else comment
            truncated += 1
    return truncated


def main():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        raw = f.read()

    listings, skipped = extract_listings(raw)
    listings, merged_count = merge_duplicates(listings)
    logs_removed = clean_logs(listings)
    comments_truncated = truncate_comments(listings)

    os.makedirs(os.path.dirname(SKIPPED_FILE), exist_ok=True)
    with open(SKIPPED_FILE, 'w', encoding='utf-8') as f:
        f.write('\n\n=====\n\n'.join(skipped))

    tmp_path = DATA_FILE + '.repair.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump({'listings': listings}, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DATA_FILE)

    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        verified = len(json.load(f)['listings'])

    print(f'Listings extracted (parsed OK): {len(listings) + merged_count}')
    print(f'Listing objects skipped (unparseable): {len(skipped)}  -> raw text saved to {SKIPPED_FILE}')
    print(f'Duplicates merged: {merged_count}')
    print(f'Log entries removed: {logs_removed}')
    print(f'Comments truncated: {comments_truncated}')
    print(f'Final listings written and re-verified as valid JSON: {verified}')


if __name__ == '__main__':
    main()
