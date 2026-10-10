"""Bounded RSS/Atom ingestion. Feed text is untrusted plain text throughout."""
import asyncio
import html
import ipaddress
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import getproxies, proxy_bypass

import httpx
from defusedxml import ElementTree

MAX_BYTES = 2_000_000


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    value = html.unescape(value or '')
    value = re.sub(r'<[^>]+>', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def canonical_url(value):
    parts = urlsplit(value.strip())
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('HTTP(S) 주소만 사용할 수 있습니다.')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', urlencode(query), ''))


async def validate_public_url(value):
    value = canonical_url(value)
    parts = urlsplit(value)
    if parts.port not in (None, 80, 443):
        raise ValueError('RSS는 80 또는 443 포트만 지원합니다.')
    host = parts.hostname.lower().rstrip('.')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError('내부 네트워크 주소는 RSS로 등록할 수 없습니다.')
        return value
    if ('.' not in host or re.fullmatch(r'[0-9.]+', host)
            or host.endswith(('.localhost', '.local', '.internal', '.test', '.invalid'))):
        raise ValueError('공개 RSS 호스트를 입력하세요.')
    proxied = bool(getproxies().get(parts.scheme)) and not proxy_bypass(host)
    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, parts.port or 443, type=socket.SOCK_STREAM), timeout=5)
    except (socket.gaierror, TimeoutError) as exc:
        # Managed egress proxies resolve allowed hosts remotely; local DNS may be intentionally disabled.
        # Literal/private/local destinations have already been rejected above.
        if proxied:
            return value
        raise ValueError('RSS 호스트를 찾을 수 없습니다.') from exc
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('내부 네트워크 주소는 RSS로 등록할 수 없습니다.')
    return value


def date_value(value):
    try:
        dt = parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        try:
            dt = datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return utcnow()
    return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def parse_feed(content, base_url):
    root = ElementTree.fromstring(content)
    local = lambda tag: tag.rsplit('}', 1)[-1]
    if local(root.tag) not in ('rss', 'RDF', 'feed'):
        raise ValueError('RSS 또는 Atom 문서가 아닙니다.')
    results = []
    for item in root.iter():
        if local(item.tag) not in ('item', 'entry'):
            continue
        values = {}
        link = ''
        for child in item:
            key = local(child.tag)
            value = ''.join(child.itertext())
            values.setdefault(key, value)
            if key == 'link':
                if child.get('href') and child.get('rel', 'alternate') == 'alternate':
                    link = child.get('href')
                elif not child.get('href') and value.strip():
                    link = value.strip()
        title = clean(values.get('title'))[:500]
        if not title or not link:
            continue
        try:
            url = canonical_url(urljoin(base_url, link))
        except ValueError:
            continue
        results.append({
            'title': title, 'url': url,
            'summary': clean(values.get('description') or values.get('summary') or values.get('encoded') or values.get('content'))[:10000],
            'published_at': date_value(values.get('pubDate') or values.get('published') or values.get('updated') or values.get('date')),
        })
        if len(results) >= 200:
            break
    return results


async def fetch_feed(url):
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers={'User-Agent': 'Medialog/0.1 RSS Reader'}) as client:
        for _ in range(5):
            url = await validate_public_url(url)
            async with client.stream('GET', url) as response:
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('RSS 리디렉션 주소가 없습니다.')
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BYTES:
                        raise ValueError('RSS 크기가 2MB 제한을 초과했습니다.')
                return parse_feed(bytes(chunks), url)
        raise ValueError('RSS 리디렉션이 너무 많습니다.')
