import re
from datetime import datetime, timezone

STOPWORDS = {'기자', '뉴스', '속보', '단독', '종합', '오늘', '이번', '대한', '위한', 'the', 'and', 'for', 'with', 'from', 'that', 'this', 'says'}


def tokens(title):
    return {w for w in re.findall(r'[가-힣A-Za-z0-9]{2,}', title.lower()) if w not in STOPWORDS}


def group_issues(articles):
    groups = []
    for article in articles:
        words = tokens(article['title'])
        match = None
        for group in groups:
            # Compare to the first article; avoid chaining unrelated stories together.
            common = words & group['_tokens']
            union = words | group['_tokens']
            if article['title'] == group['title'] or (len(common) >= 2 and len(common) / max(len(union), 1) >= 0.30):
                match = group
                break
        if match is None:
            match = {'id': article['id'], 'title': article['title'], '_tokens': words, 'articles': []}
            groups.append(match)
        match['articles'].append(article)
    now = datetime.now(timezone.utc)
    for group in groups:
        group.pop('_tokens')
        group['publisher_count'] = len({a['publisher'] for a in group['articles']})
        newest = max(datetime.fromisoformat(a['published_at']) for a in group['articles'])
        age = max(0, (now - newest).total_seconds() / 3600)
        group['score'] = round(group['publisher_count'] * 10 + min(len(group['articles']), 10) * 2 + max(0, 24 - age), 1)
        group['needs_review'] = not any(len(a['summary']) >= 80 for a in group['articles'])
    return sorted(groups, key=lambda g: g['score'], reverse=True)


def draft_project(articles, outline=None, design=None):
    from .design import default_outline, Design
    outline = outline or [row.model_dump() for row in default_outline()]
    design = design or Design().model_dump()
    primary = articles[0]
    sources = [{'article_id': a['id'], 'publisher': a['publisher'], 'title': a['title'], 'url': a['url']} for a in articles]
    excerpts = []
    for article in articles:
        sentences = re.split(r'(?<=[.!?。])\s+|(?<=다\.)\s*', article['summary'])
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) >= 20 and sentence not in [e[0] for e in excerpts]:
                excerpts.append((sentence[:450], article['id']))
    cards = []
    excerpt_index = 0
    for item in outline:
        role = item['role']
        if role == 'cover':
            body, source_ids = primary['title'][:450], [primary['id']]
        elif role == 'sources':
            body = '\n'.join(dict.fromkeys(a['publisher'] for a in articles))[:450]
            source_ids = [a['id'] for a in articles]
        elif role != 'custom' and excerpt_index < len(excerpts):
            body, source_id = excerpts[excerpt_index]
            source_ids = [source_id]
            excerpt_index += 1
        else:
            body, source_ids = '', []
        cards.append({'role': role, 'heading': item['heading'], 'body': body, 'source_ids': source_ids,
                      'layout': None, 'image_id': design['image_ids'][0] if design['image_ids'] else None})
    caption = primary['title'] + '\n\n'
    for source in sources:
        entry = f"출처: {source['publisher']}\n{source['url']}\n"
        if len(caption) + len(entry) > 10000:
            break
        caption += entry
    return {'title': primary['title'], 'cards': cards, 'sources': sources, 'accent': design['accent'], 'design': design, 'reviewed': False,
            'caption': caption.rstrip(),
            'generation_mode': 'extractive', 'warning': 'RSS 요약 문장을 발췌한 초안입니다. 빈 카드에 확인한 내용을 작성하고 모든 출처와 사실을 검토하세요.'}
