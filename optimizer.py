#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3U Logo Optimizer Test Motoru

Ana hedef:
- tvg-logo URL'lerini benzersiz indirip liste-logo/ klasörüne almak
- liste-logolar.json üretmek
- M3U başına x-logo-source="liste-logolar.json" eklemek
- EXTINF satırlarından tvg-logo alanlarını silmek
- logo-id alanını başlıktan üreterek eklemek
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import gzip
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

ATTR_RE = re.compile(r'([A-Za-z0-9_:-]+)="([^"]*)"')
TVG_LOGO_RE = re.compile(r'\s*tvg-logo="[^"]*"')
LOGO_ID_RE = re.compile(r'\s*logo-id="[^"]*"')
EXTM3U_RE = re.compile(r'^#EXTM3U\b')

TURKISH_MAP = str.maketrans({
    'ı': 'i', 'İ': 'I', 'ğ': 'g', 'Ğ': 'G', 'ü': 'u', 'Ü': 'U',
    'ş': 's', 'Ş': 'S', 'ö': 'o', 'Ö': 'O', 'ç': 'c', 'Ç': 'C',
})

IMAGE_EXT_BY_CONTENT_TYPE = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/webp': '.webp',
    'image/gif': '.gif',
    'image/svg+xml': '.svg',
    'image/bmp': '.bmp',
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_attrs(extinf_line: str) -> Dict[str, str]:
    return {m.group(1): m.group(2) for m in ATTR_RE.finditer(extinf_line)}


def extract_title(extinf_line: str, attrs: Dict[str, str]) -> str:
    if ',' in extinf_line:
        title = extinf_line.rsplit(',', 1)[1].strip()
        if title:
            return title
    for key in ('tvg-name', 'name', 'title', 'group-title'):
        val = attrs.get(key, '').strip()
        if val:
            return val
    return 'LOGO'


def slugify_title(title: str, max_len: int = 80) -> str:
    cleaned = title.strip().translate(TURKISH_MAP)
    cleaned = unicodedata.normalize('NFKD', cleaned)
    cleaned = ''.join(ch for ch in cleaned if not unicodedata.combining(ch))
    cleaned = cleaned.upper()
    cleaned = re.sub(r'[^A-Z0-9]+', '_', cleaned)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    if not cleaned:
        cleaned = 'LOGO'
    return cleaned[:max_len].strip('_') or 'LOGO'


def url_hash(url: str, length: int = 12) -> str:
    return hashlib.sha1(url.encode('utf-8', errors='ignore')).hexdigest()[:length]


def guess_ext_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    ext = Path(path).suffix
    if ext in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.bmp'}:
        return '.jpg' if ext == '.jpeg' else ext
    guessed, _ = mimetypes.guess_type(path)
    return IMAGE_EXT_BY_CONTENT_TYPE.get(guessed or '', '.jpg')


def unique_logo_id(base: str, url: str, used_ids: Dict[str, str]) -> str:
    # Aynı base + aynı URL ise tekrar aynı ID döner; aynı base ama farklı URL ise hash eklenir.
    existing_url = used_ids.get(base)
    if existing_url is None:
        used_ids[base] = url
        return base
    if existing_url == url:
        return base
    candidate = f'{base}_{url_hash(url, 6).upper()}'
    idx = 2
    while candidate in used_ids and used_ids[candidate] != url:
        candidate = f'{base}_{url_hash(url, 6).upper()}_{idx}'
        idx += 1
    used_ids[candidate] = url
    return candidate


def add_logo_id_and_remove_tvg_logo(line: str, logo_id: str) -> str:
    line = TVG_LOGO_RE.sub('', line)
    line = LOGO_ID_RE.sub('', line)
    if line.startswith('#EXTINF:'):
        # #EXTINF:-1 sonrasına logo-id ekle; mevcut attribute sırasına minimum müdahale.
        line = re.sub(r'^(#EXTINF:[^\s,]+)', rf'\1 logo-id="{logo_id}"', line, count=1)
    return line


def add_x_logo_source(line: str, source_name: str) -> str:
    if not EXTM3U_RE.match(line):
        return line
    # Eski x-logo-source varsa yenile.
    line = re.sub(r'\s*x-logo-source="[^"]*"', '', line)
    return line.rstrip() + f' x-logo-source="{source_name}"'


def find_latest_input(input_dir: Path) -> Optional[Path]:
    candidates = []
    for pattern in ('*.m3u', '*.m3u8', '*.m3u.gz', '*.m3u8.gz', '*.gz'):
        candidates.extend(input_dir.glob(pattern))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)




def open_input_binary(input_path: Path):
    if input_path.name.lower().endswith('.gz'):
        return gzip.open(input_path, 'rb')
    return input_path.open('rb')

def read_text_line(line_bytes: bytes) -> str:
    # Büyük ve karışık listelerde kırılmadan devam etmek için toleranslı decode.
    try:
        return line_bytes.decode('utf-8')
    except UnicodeDecodeError:
        return line_bytes.decode('utf-8', errors='replace')


def download_one(url: str, target: Path, timeout: int, max_bytes: int) -> Dict[str, object]:
    if target.exists() and target.stat().st_size > 0:
        return {'ok': True, 'url': url, 'path': str(target), 'status': 'exists', 'bytes': target.stat().st_size}

    request = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (LogoManager M3U Optimizer Test)',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            data = bytearray()
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_bytes:
                    return {'ok': False, 'url': url, 'path': str(target), 'error': f'max_bytes_exceeded>{max_bytes}'}
            if not data:
                return {'ok': False, 'url': url, 'path': str(target), 'error': 'empty_response'}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bytes(data))
            return {'ok': True, 'url': url, 'path': str(target), 'status': 'downloaded', 'bytes': len(data), 'contentType': content_type}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'url': url, 'path': str(target), 'error': str(exc)[:500]}


def optimize(
    input_path: Path,
    output_dir: Path,
    logo_dir: Path,
    logo_json_path: Path,
    x_logo_source: Optional[str] = None,
    download_logos: bool = True,
    max_downloads: int = 0,
    max_workers: int = 8,
    timeout: int = 20,
    max_logo_bytes: int = 8_000_000,
) -> Dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    logo_dir.mkdir(parents=True, exist_ok=True)

    output_m3u = output_dir / 'optimized.m3u'
    report_path = output_dir / 'report.json'
    source_name = x_logo_source or logo_json_path.name

    used_ids: Dict[str, str] = {}
    url_to_local: Dict[str, str] = {}
    items: Dict[str, Dict[str, object]] = {}
    url_usage: Dict[str, int] = {}

    stats = {
        'inputFile': str(input_path),
        'uploadedBytes': input_path.stat().st_size if input_path.exists() else 0,
        'inputBytes': 0,
        'totalLines': 0,
        'extinfLines': 0,
        'streamLines': 0,
        'logoLines': 0,
        'noLogoExtinfLines': 0,
        'uniqueLogoUrls': 0,
        'items': 0,
        'downloaded': 0,
        'downloadFailed': 0,
        'skippedDownload': 0,
        'startedAt': now_iso(),
    }

    current_extinf_had_logo = False

    with open_input_binary(input_path) as src, output_m3u.open('w', encoding='utf-8', newline='\n') as out:
        first_line_done = False
        for raw in src:
            stats['totalLines'] += 1
            stats['inputBytes'] += len(raw)
            line = read_text_line(raw).rstrip('\r\n')

            if not first_line_done:
                first_line_done = True
                if EXTM3U_RE.match(line):
                    out.write(add_x_logo_source(line, source_name) + '\n')
                    continue
                out.write(f'#EXTM3U x-logo-source="{source_name}"\n')

            if line.startswith('#EXTINF'):
                stats['extinfLines'] += 1
                attrs = parse_attrs(line)
                logo_url = attrs.get('tvg-logo', '').strip()
                if logo_url:
                    stats['logoLines'] += 1
                    url_usage[logo_url] = url_usage.get(logo_url, 0) + 1

                    title = extract_title(line, attrs)
                    base_id = slugify_title(title)
                    logo_id = unique_logo_id(base_id, logo_url, used_ids)
                    group = attrs.get('group-title', '')
                    tvg_name = attrs.get('tvg-name', '')

                    if logo_url not in url_to_local:
                        ext = guess_ext_from_url(logo_url)
                        local_file = f'liste-logo/{url_hash(logo_url, 12)}{ext}'
                        url_to_local[logo_url] = local_file
                    else:
                        local_file = url_to_local[logo_url]

                    item = items.setdefault(logo_id, {
                        'logoId': logo_id,
                        'title': title,
                        'tvgName': tvg_name,
                        'group': group,
                        'originalLogoUrl': logo_url,
                        'localFile': local_file,
                        'usedCount': 0,
                        'downloaded': False,
                    })
                    item['usedCount'] = int(item.get('usedCount', 0)) + 1

                    out.write(add_logo_id_and_remove_tvg_logo(line, logo_id) + '\n')
                else:
                    stats['noLogoExtinfLines'] += 1
                    out.write(line + '\n')
                current_extinf_had_logo = bool(logo_url)
            else:
                if line and not line.startswith('#'):
                    stats['streamLines'] += 1
                out.write(line + '\n')

    stats['uniqueLogoUrls'] = len(url_to_local)
    stats['items'] = len(items)

    download_results: List[Dict[str, object]] = []
    if download_logos and url_to_local:
        urls = list(url_to_local.keys())
        if max_downloads and max_downloads > 0:
            urls = urls[:max_downloads]
            stats['skippedDownload'] = len(url_to_local) - len(urls)

        def task(url: str) -> Dict[str, object]:
            local_rel = url_to_local[url]
            return download_one(url, Path(local_rel), timeout=timeout, max_bytes=max_logo_bytes)

        # Working directory repo root olmalı; localRel liste-logo/... bu yüzden doğru yere yazar.
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            for result in pool.map(task, urls):
                download_results.append(result)

        ok_urls = {r['url'] for r in download_results if r.get('ok')}
        for item in items.values():
            if item.get('originalLogoUrl') in ok_urls:
                item['downloaded'] = True
        stats['downloaded'] = sum(1 for r in download_results if r.get('ok'))
        stats['downloadFailed'] = sum(1 for r in download_results if not r.get('ok'))
    else:
        stats['skippedDownload'] = len(url_to_local)

    stats['outputBytes'] = output_m3u.stat().st_size
    stats['savedBytesInM3U'] = max(0, int(stats['inputBytes']) - int(stats['outputBytes']))
    stats['finishedAt'] = now_iso()

    if source_name.startswith('http://') or source_name.startswith('https://'):
        raw_base = source_name.rsplit('/', 1)[0]
        for item in items.values():
            local_file = str(item.get('localFile', ''))
            if local_file:
                item['githubRawUrl'] = f'{raw_base}/{local_file}'

    logo_json = {
        'schema': 'logo-manager-list-logo-source-v1',
        'sourceM3U': input_path.name,
        'createdAt': stats['startedAt'],
        'updatedAt': stats['finishedAt'],
        'xLogoSource': source_name,
        'logoFolder': 'liste-logo',
        'outputM3U': 'output/optimized.m3u',
        'stats': stats,
        'items': dict(sorted(items.items(), key=lambda kv: kv[0])),
    }
    logo_json_path.write_text(json.dumps(logo_json, ensure_ascii=False, indent=2), encoding='utf-8')

    report = {
        'stats': stats,
        'downloadResultsSample': download_results[:200],
        'failedDownloadsSample': [r for r in download_results if not r.get('ok')][:200],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='M3U tvg-logo -> x-logo-source + logo-id optimizer')
    parser.add_argument('input', nargs='?', help='M3U input path. .m3u, .m3u8 veya .gz olabilir. Boşsa input/ içindeki en yeni dosya kullanılır.')
    parser.add_argument('--output-dir', default='output')
    parser.add_argument('--logo-dir', default='liste-logo')
    parser.add_argument('--logo-json', default='liste-logolar.json')
    parser.add_argument('--no-download', action='store_true', help='Logo indirme; sadece JSON ve M3U üret.')
    parser.add_argument('--x-logo-source', default=os.getenv('X_LOGO_SOURCE', ''), help='M3U #EXTM3U satırına yazılacak x-logo-source adresi. Boşsa GitHub Actions içinde raw link otomatik üretilir.')
    parser.add_argument('--max-downloads', type=int, default=int(os.getenv('MAX_DOWNLOADS', '0')), help='0 = limitsiz')
    parser.add_argument('--workers', type=int, default=int(os.getenv('MAX_WORKERS', '8')))
    parser.add_argument('--timeout', type=int, default=int(os.getenv('REQUEST_TIMEOUT', '20')))
    parser.add_argument('--max-logo-bytes', type=int, default=int(os.getenv('MAX_LOGO_BYTES', '8000000')))
    args = parser.parse_args(argv)

    input_path = Path(args.input) if args.input else find_latest_input(Path('input'))
    if not input_path:
        print('HATA: input/ içinde .m3u veya .m3u8 dosyası bulunamadı.', file=sys.stderr)
        return 2
    if not input_path.exists():
        print(f'HATA: Dosya bulunamadı: {input_path}', file=sys.stderr)
        return 2

    x_logo_source = args.x_logo_source.strip()
    if not x_logo_source:
        repo = os.getenv('GITHUB_REPOSITORY', '').strip()
        branch = os.getenv('GITHUB_REF_NAME', '').strip() or 'main'
        if repo:
            x_logo_source = f'https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{args.logo_json}'

    stats = optimize(
        input_path=input_path,
        output_dir=Path(args.output_dir),
        logo_dir=Path(args.logo_dir),
        logo_json_path=Path(args.logo_json),
        x_logo_source=x_logo_source or None,
        download_logos=not args.no_download,
        max_downloads=args.max_downloads,
        max_workers=args.workers,
        timeout=args.timeout,
        max_logo_bytes=args.max_logo_bytes,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
