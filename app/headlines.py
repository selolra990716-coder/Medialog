"""Conservative title shortening: remove feed labels and ornamental clauses."""
import re


def summarize_title(title, limit=48):
    text = re.sub(r'\s+', ' ', title).strip()
    # Editorial labels add length but do not describe the event.
    text = re.sub(r'^\s*(?:\[(?:속보|종합\d*|단독|영상|사진|포토|데모)\]\s*)+', '', text)
    text = re.sub(r'\s*[\[(](?:종합\d*|속보|영상|사진|포토)[\])]\s*$', '', text)
    text = re.sub(r'[‘’“”「」]', '', text)
    text = re.sub(r'\s*행진\s*$', '', text)
    text = re.sub(r'\b\d{2,3}\s*[-–:]\s*\d{2,3}\s*(?:역전승|승리|승)\b[, ]*', '', text)
    clauses = re.split(r'\s*(?:…+|\.{3,})\s*', text)
    if len(clauses) > 1:
        lead, event = clauses[0], clauses[-1]
        # Keep the explicitly named subject when a promotional lead precedes facts.
        match = re.fullmatch(r'.+이 먹히는\s+(.+)', lead)
        if match and event:
            text = f'{match.group(1)}, {event}'
        else:
            # Do not throw away actors, numbers, or attribution from other clauses.
            text = ' · '.join(clause for clause in clauses if clause)
    if len(text) <= limit:
        return text
    # A clearly marked excerpt is safer than inventing a missing fact.
    available = limit - 1
    cut = text.rfind(' ', 0, available + 1)
    if cut < available // 2:
        cut = available
    return text[:cut].rstrip(' ,·') + '…'


def format_card_body(text):
    """Remove only leading wire-service bylines, then separate full sentences."""
    text = text.strip()
    text = re.sub(r'^\([^\n)]{1,60}[-=][^\n)]{1,60}\)\s*', '', text)
    text = re.sub(r'^(?:[가-힣]{2,5}\s+){1,5}(?:기자|특파원)\s*=\s*', '', text)
    paragraphs = []
    for paragraph in re.split(r'\n\s*\n|\n', text):
        paragraph = re.sub(r'[ \t]+', ' ', paragraph).strip()
        if not paragraph:
            continue
        paragraphs.extend(re.split(r'(?<=[.!?。])\s+(?=[가-힣A-Z“\"\'‘])', paragraph))
    if len(paragraphs) > 6:
        paragraphs = [' '.join(paragraphs[index:index+2]) for index in range(0, len(paragraphs), 2)]
    return '\n\n'.join(paragraphs)
