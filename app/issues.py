import re
import math
from .headlines import summarize_title, format_card_body
from datetime import datetime, timezone

STOPWORDS = {'기자', '뉴스', '속보', '단독', '종합', '오늘', '이번', '대한', '위한', 'the', 'and', 'for', 'with', 'from', 'that', 'this', 'says'}


def tokens(title):
    return {w for w in re.findall(r'[가-힣A-Za-z0-9]{2,}', title.lower()) if w not in STOPWORDS}


def noteworthy(title):
    return not re.search(r'^\s*\[(?:부고|인사|게시판|오늘의\s*일정|[^\]]*(?:중간순위|전적))\]', title)


def group_issues(articles):
    groups = []
    for article in articles:
        if not noteworthy(article['title']):continue
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
        group['local_clicks']=sum(int(a.get('open_count') or 0) for a in group['articles'])
        group['priority_basis']='복수 언론사 보도·최신성·프로그램 내 클릭'
        group['score'] = round(min(20,math.log1p(group['local_clicks'])*4) + group['publisher_count'] * 10 + min(len(group['articles']), 10) * 2 + max(0, 24 - age), 1)
        excerpts = collect_excerpts(group['articles'])
        selected = []
        for excerpt in excerpts:
            if len(' '.join(text for text, _ in selected + [excerpt])) <= 900:
                selected.append(excerpt)
            if len(selected) == 3:
                break
        group['summary'] = ' '.join(text for text, _ in selected)
        group['summary_source_ids'] = list(dict.fromkeys(id for _, id in selected))
        group['needs_review'] = sum(len(text) for text, _ in excerpts) < 80
    return sorted(groups, key=lambda g: g['score'], reverse=True)


def draft_project(articles, outline=None, design=None):
    from .design import default_outline, Design
    outline = outline or [row.model_dump() for row in default_outline()]
    design = design or Design().model_dump()
    design = {**design, 'image_ids': list(design.get('image_ids', []))}
    for article in articles:
        if article.get('image_id') and article['image_id'] not in design['image_ids'] and len(design['image_ids']) < 10:
            design['image_ids'].append(article['image_id'])
    primary = articles[0]
    sources = [{'article_id': a['id'], 'publisher': a['publisher'], 'title': a['title'], 'url': a['url']} for a in articles]
    excerpts = collect_excerpts(articles)
    cards = []
    excerpt_index = 0
    for item in outline:
        role = item['role']
        if role == 'cover':
            body, source_ids = summarize_title(primary['title']), [primary['id']]
        elif role == 'sources':
            body = '\n'.join(dict.fromkeys(a['publisher'] for a in articles))[:450]
            source_ids = [a['id'] for a in articles]
        elif role == 'impact':
            body, source_ids = impact_excerpt(articles)
        elif role != 'custom' and excerpt_index < len(excerpts):
            selected = []
            for text, source_id in excerpts[excerpt_index:]:
                if len('\n\n'.join([entry[0] for entry in selected] + [text])) > 500:
                    break
                selected.append((text, source_id))
                if len(selected) == (3 if len(outline) == 3 else 1):
                    break
            body = '\n\n'.join(text for text, _ in selected)
            source_ids = list(dict.fromkeys(source_id for _, source_id in selected))
            excerpt_index += len(selected)
        else:
            body, source_ids = '', []
        photo = next((a.get('image_id') for a in articles if a['id'] in source_ids and a.get('image_id')), None)
        if not photo:
            photo = next((a.get('image_id') for a in articles if a.get('image_id')), None)
        cards.append({'role': role, 'heading': item['heading'], 'body': format_card_body(body) if role not in ('cover', 'sources', 'custom') else body, 'source_ids': source_ids,
                      'layout': None, 'image_id': photo or (design['image_ids'][0] if design['image_ids'] and design.get('layout') not in ('breaking', 'poster') else None)})
    caption = primary['title'] + '\n\n'
    for source in sources:
        entry = f"출처: {source['publisher']}\n{source['url']}\n"
        if len(caption) + len(entry) > 10000:
            break
        caption += entry
    return {'title': primary['title'], 'cards': cards, 'sources': sources, 'accent': design['accent'], 'design': design, 'reviewed': False,
            'caption': caption.rstrip(),
            'generation_mode': 'extractive', 'warning': 'RSS 요약 문장을 발췌한 초안입니다. 빈 카드에 확인한 내용을 작성하고 모든 출처와 사실을 검토하세요.'}


def collect_excerpts(articles):
    collected = []
    seen = set()
    by_article = []
    for article in articles:
        candidates = []
        for sentence in re.split(r'(?<=[.!?。])\s+|(?<=다\.)\s*', format_card_body(article['summary'])):
            sentence = re.sub(r'\s+', ' ', sentence).strip()
            if len(sentence) < 20 or re.search(r'무단\s*(전재|복제)|재배포\s*금지|All rights reserved|Copyright', sentence, re.I):
                continue
            key = re.sub(r'[^가-힣a-z0-9]', '', sentence.lower())
            if key in seen:
                continue
            seen.add(key)
            candidates.append((sentence if len(sentence) <= 450 else sentence[:447].rstrip() + '…', article['id']))
        by_article.append(candidates)
    # Alternate sources before taking a second sentence from one publisher.
    for index in range(max((len(rows) for rows in by_article), default=0)):
        for rows in by_article:
            if index < len(rows):
                collected.append(rows[index])
    return collected


def impact_excerpt(articles):
    pattern = r'전망|예정|계획|영향|기대|우려|향후|앞으로|다음\s*(?:주|달|경기)|가능성|전환점|부담|과제|\b(?:plan|plans|planned|forecast|outlook|expected|impact)\b'
    candidates = [(text, article_id) for text, article_id in collect_excerpts(articles)
                  if re.search(pattern, text, re.I)]
    if candidates:
        selected = []
        for text, article_id in candidates:
            if len('\n\n'.join([entry[0] for entry in selected] + [text])) <= 500:
                selected.append((text,article_id))
            if len(selected) == 3:
                break
        return '\n\n'.join(format_card_body(text) for text, _ in selected), list(dict.fromkeys(id for _, id in selected))
    return '', []
