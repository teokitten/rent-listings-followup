import os
import re
import json
import uuid
import shutil
import threading
from datetime import datetime, timezone
from glob import glob

from flask import Flask, request, jsonify, render_template, send_file, Response
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

_data_lock = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
DATA_FILE = os.path.join(DATA_DIR, 'listings.json')

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0'

STATUS_LABELS = {
    'rented_out': 'Already Rented Out',
    'no_foreigners': 'Will Not Accept Foreign Tenants',
    'not_pursuing': 'We Decided Not to Pursue',
    'not_suitable': 'Not Suitable – We Rejected After Viewing',
    'landlord_declined': 'Landlord Declined After Viewing',
    'pending_contact': 'Pending First Contact',
    'to_call_back': 'To Call Back',
    'backup': 'Leave as Backup',
    'dropped_after_viewing': 'Dropped After Viewing',
    'reserved': 'Reserved',
    'inperson_only': 'In-Person Viewings Only',
    'no_response': 'Stale – No Answer',
    'to_review': 'To Review',
    'on_hold': 'On Hold',
    'waiting_vacancy': 'Waiting for Current Tenant to Leave',
    'form_submitted': 'Application / Form Submitted – Awaiting Reply',
    'viewing_scheduled': 'Viewing Scheduled',
    'viewed_pending': 'Viewed – Decision Pending',
}

app = Flask(__name__)


def ensure_storage():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_listings([])


def load_data():
    ensure_storage()
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_listings(listings):
    tmp_path = f'{DATA_FILE}.{uuid.uuid4().hex}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump({'listings': listings}, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, DATA_FILE)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def find_listing(data, listing_id):
    for listing in data['listings']:
        if listing['id'] == listing_id:
            return listing
    return None


@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response


@app.route('/api/<path:_rest>', methods=['OPTIONS'])
def options_handler(_rest):
    return Response(status=204)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

MIN_IMAGE_DIMENSION = 200


def _meta_images_with_dimensions(soup, prop_name):
    """Walk <meta> tags in document order, pairing a meta tag (matched by its
    'property' or 'name' attribute) with any immediately-following
    '{prop_name}:width' / '{prop_name}:height' meta tags. Returns a list of
    {'url': ..., 'width': int|None, 'height': int|None} dicts in document order.
    """
    results = []
    current = None
    width_key = f'{prop_name}:width'
    height_key = f'{prop_name}:height'
    for tag in soup.find_all('meta'):
        key = tag.get('property') or tag.get('name')
        if key == prop_name:
            content = tag.get('content')
            if content:
                current = {'url': content, 'width': None, 'height': None}
                results.append(current)
        elif key == width_key and current is not None:
            try:
                current['width'] = int(tag.get('content', ''))
            except (TypeError, ValueError):
                pass
        elif key == height_key and current is not None:
            try:
                current['height'] = int(tag.get('content', ''))
            except (TypeError, ValueError):
                pass
    return results


def extract_images_from_html(soup, url):
    """Cross-domain image extraction, in priority order:
    1. og:image meta tags
    2. twitter:image meta tags
    3. For sreality.cz: img tags inside carousel/gallery-classed elements,
       plus JSON-LD structured data image arrays
    4. Deduplicate, drop images known (via companion width/height meta tags)
       to be smaller than MIN_IMAGE_DIMENSION px, and if that leaves nothing,
       fall back to just the first og:image found (if any).
    """
    og_images = _meta_images_with_dimensions(soup, 'og:image')
    twitter_images = _meta_images_with_dimensions(soup, 'twitter:image')

    extra_images = []
    if 'sreality.cz' in url:
        for el in soup.find_all(class_=re.compile(r'(carousel|gallery)', re.I)):
            for img in el.find_all('img'):
                src = img.get('src') or img.get('data-src')
                if src:
                    extra_images.append({'url': src, 'width': None, 'height': None})

        for script_tag in soup.find_all('script', attrs={'type': 'application/ld+json'}):
            try:
                ld_data = json.loads(script_tag.string or '')
            except Exception:
                continue
            candidates = ld_data if isinstance(ld_data, list) else [ld_data]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                img_field = item.get('image')
                if isinstance(img_field, list):
                    extra_images.extend(
                        {'url': i, 'width': None, 'height': None}
                        for i in img_field if isinstance(i, str)
                    )
                elif isinstance(img_field, str):
                    extra_images.append({'url': img_field, 'width': None, 'height': None})

    all_images = og_images + twitter_images + extra_images

    seen = set()
    deduped = []
    for img in all_images:
        if img['url'] and img['url'] not in seen:
            seen.add(img['url'])
            deduped.append(img)

    def big_enough(img):
        if img['width'] is not None and img['width'] < MIN_IMAGE_DIMENSION:
            return False
        if img['height'] is not None and img['height'] < MIN_IMAGE_DIMENSION:
            return False
        return True

    filtered = [img['url'] for img in deduped if big_enough(img)]

    if filtered:
        return filtered[:3]

    if og_images:
        return [og_images[0]['url']]

    return []


def extract_sreality(url):
    match = re.search(r'/(\d+)(?:\?|$)', url)
    if not match:
        return extract_generic(url)

    listing_id = match.group(1)
    headers = {
        'User-Agent': USER_AGENT,
        'Accept': 'application/json',
        'Referer': 'https://www.sreality.cz/',
    }

    try:
        api_url = f'https://www.sreality.cz/api/cs/v2/estates/{listing_id}'
        resp = requests.get(api_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise ValueError('non-200 status')
        payload = resp.json()

        images = []
        for image in payload.get('_embedded', {}).get('images', []):
            href = None
            links = image.get('_links', {})
            if 'self' in links:
                href = links['self'].get('href')
            elif 'gallery' in links:
                href = links['gallery'].get('href')
            if href:
                images.append(href)
            if len(images) >= 3:
                break

        title = payload.get('name', {}).get('value', '') or ''

        price = ''
        try:
            value_raw = payload.get('price_czk', {}).get('value_raw', '')
            if value_raw:
                price = f"{value_raw:,} CZK/month".replace(",", " ")
        except (TypeError, ValueError):
            price = ''
        if not price:
            price = payload.get('price', {}).get('value', '') or ''

        return {'title': title, 'price': price, 'images': images}
    except Exception:
        return extract_sreality_fallback(url, headers)


def extract_sreality_fallback(url, headers):
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        images = extract_images_from_html(soup, url)
        title_tag = soup.find('meta', attrs={'property': 'og:title'})
        title = title_tag.get('content', '') if title_tag else ''
        return {'title': title or '', 'price': '', 'images': images}
    except Exception:
        return {'title': '', 'price': '', 'images': []}


def extract_generic(url):
    headers = {'User-Agent': USER_AGENT}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')
        images = extract_images_from_html(soup, url)
        title_tag = soup.find('meta', attrs={'property': 'og:title'})
        title = title_tag.get('content', '') if title_tag else ''
        return {'title': title or '', 'price': '', 'images': images}
    except Exception:
        return {'title': '', 'price': '', 'images': []}


def extract_listing_data(url):
    try:
        if 'sreality.cz' in url:
            return extract_sreality(url)
        return extract_generic(url)
    except Exception:
        return {'title': '', 'price': '', 'images': []}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/listings', methods=['GET'])
def get_listings():
    return jsonify(load_data())


@app.route('/api/listings', methods=['POST'])
def create_listing():
    body = request.get_json(force=True, silent=True) or {}
    url = body.get('url')
    if not url:
        return jsonify({'error': 'url is required'}), 400

    extracted = extract_listing_data(url)

    listing = {
        'id': str(uuid.uuid4()),
        'url': url,
        'title': extracted.get('title', ''),
        'price': extracted.get('price', ''),
        'date_added': body.get('date_added') or datetime.now().strftime('%Y-%m-%d'),
        'status': body.get('status') or 'pending_contact',
        'next_action': '',
        'images': extracted.get('images', []),
        'comments': body.get('comments', ''),
        'log': [
            {'timestamp': now_iso(), 'type': 'created', 'content': 'Listing added'}
        ],
    }

    with _data_lock:
        data = load_data()
        data['listings'].append(listing)
        save_listings(data['listings'])

    return jsonify(listing)


@app.route('/api/listings/<listing_id>', methods=['PUT'])
def update_listing(listing_id):
    body = request.get_json(force=True, silent=True) or {}
    with _data_lock:
        data = load_data()
        listing = find_listing(data, listing_id)
        if not listing:
            return jsonify({'error': 'listing not found'}), 404

        if 'status' in body and body['status'] != listing.get('status'):
            new_status = body['status']
            listing['status'] = new_status
            note = body.get('note', '')
            label = STATUS_LABELS.get(new_status, new_status)
            content = f"Status changed to {label}."
            if note:
                content += f" Note: {note}"
            else:
                content += " Note:"
            listing['log'].append({'timestamp': now_iso(), 'type': 'status_change', 'content': content})

        if 'next_action' in body and body['next_action'] != listing.get('next_action'):
            listing['next_action'] = body['next_action']
            listing['log'].append({
                'timestamp': now_iso(),
                'type': 'field_update',
                'content': f"Next action updated to: {body['next_action']}",
            })

        if 'comments' in body and body['comments'] != listing.get('comments'):
            listing['comments'] = body['comments']

        for field in ('date_added', 'title', 'price', 'viewing_date', 'viewing_time'):
            if field in body and body[field] != listing.get(field):
                listing[field] = body[field]
                listing['log'].append({
                    'timestamp': now_iso(),
                    'type': 'field_update',
                    'content': f"{field} updated to {body[field]}",
                })

        for field in ('viewing_comment', 'is_favorite'):
            if field in body:
                listing[field] = body[field]

        if 'log' in body and isinstance(body['log'], list):
            listing['log'] = body['log']

        save_listings(data['listings'])
    return jsonify(listing)


@app.route('/api/listings/<listing_id>/log', methods=['POST'])
def add_log_entry(listing_id):
    body = request.get_json(force=True, silent=True) or {}
    content = body.get('content', '')

    with _data_lock:
        data = load_data()
        listing = find_listing(data, listing_id)
        if not listing:
            return jsonify({'error': 'listing not found'}), 404

        listing['log'].append({'timestamp': now_iso(), 'type': 'note', 'content': content})
        save_listings(data['listings'])
    return jsonify(listing)


@app.route('/api/listings/<listing_id>', methods=['DELETE'])
def delete_listing(listing_id):
    with _data_lock:
        data = load_data()
        before = len(data['listings'])
        data['listings'] = [l for l in data['listings'] if l['id'] != listing_id]
        if len(data['listings']) == before:
            return jsonify({'error': 'listing not found'}), 404
        save_listings(data['listings'])
    return jsonify({'success': True})


@app.route('/api/extract', methods=['GET'])
def extract_route():
    url = request.args.get('url', '')
    if not url:
        return jsonify({'title': '', 'price': '', 'images': []})
    result = extract_listing_data(url)
    return jsonify(result)


@app.route('/api/export/json', methods=['GET'])
def export_json():
    ensure_storage()
    return send_file(
        DATA_FILE,
        mimetype='application/json',
        as_attachment=True,
        download_name='rent-listings-export.json',
    )


@app.route('/api/import/json', methods=['POST'])
def import_json():
    if 'file' not in request.files:
        return jsonify({'error': 'file is required'}), 400
    file = request.files['file']
    try:
        imported = json.load(file.stream)
    except Exception:
        return jsonify({'error': 'invalid JSON file'}), 400

    if not isinstance(imported, dict) or 'listings' not in imported or not isinstance(imported['listings'], list):
        return jsonify({'error': 'invalid listings format'}), 400

    with _data_lock:
        save_listings(imported['listings'])
    return jsonify({'success': True, 'count': len(imported['listings'])})


@app.route('/api/backup/trigger', methods=['GET'])
def trigger_backup():
    filename = backup_job()
    return jsonify({'success': True, 'filename': filename})


# ---------------------------------------------------------------------------
# Backup job
# ---------------------------------------------------------------------------

def backup_job():
    ensure_storage()
    today = datetime.now().strftime('%Y-%m-%d')
    filename = f'listings_backup_{today}.json'
    dest = os.path.join(BACKUP_DIR, filename)
    shutil.copyfile(DATA_FILE, dest)

    backups = sorted(glob(os.path.join(BACKUP_DIR, 'listings_backup_*.json')))
    excess = len(backups) - 30
    if excess > 0:
        for old_file in backups[:excess]:
            os.remove(old_file)

    return filename


ensure_storage()

if __name__ == '__main__':
    if not os.environ.get('WERKZEUG_RUN_MAIN'):
        scheduler = BackgroundScheduler(timezone='Europe/Prague')
        scheduler.add_job(backup_job, 'cron', hour=23, minute=0)
        scheduler.start()
    app.run(debug=True, use_reloader=True, port=5000)
