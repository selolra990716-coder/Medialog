import io
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from .assets import image_path

FONT_CANDIDATES = [
    str(Path(__file__).resolve().parent.parent / 'fonts' / 'NotoSansCJK-Regular.ttc'),
    str(Path(os.getenv('WINDIR', 'C:/Windows')) / 'Fonts' / 'malgun.ttf'),
    '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
]


def font_path():
    configured = os.getenv('MEDIALOG_FONT')
    if configured:
        if not Path(configured).is_file():
            raise ValueError('MEDIALOG_FONT 파일을 찾을 수 없습니다.')
        return configured
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    raise ValueError('카드 렌더링에 필요한 글꼴이 없습니다. MEDIALOG_FONT를 설정하세요.')


def font(size):
    # Noto TTC index 1 is Korean; using index 0 also renders Korean glyphs.
    return ImageFont.truetype(font_path(), size)


def wrap(text, draw, face, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for char in paragraph:
            if line and draw.textlength(line + char, font=face) > width:
                lines.append(line.rstrip())
                line = char.lstrip()
            else:
                line += char
        lines.append(line)
    return lines


def fit(text, draw, width, height, max_size, min_size):
    for size in range(max_size, min_size - 1, -2):
        face = font(size)
        lines = wrap(text, draw, face, width)
        spacing = int(size * 1.5)
        if len(lines) * spacing <= height:
            return face, lines, spacing
    raise ValueError('카드 문구가 이미지 영역보다 깁니다. 문구를 줄여 주세요.')


def render_card(project, index):
    card = project['cards'][index]
    accent = project['accent']
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent):
        raise ValueError('강조색 형식이 잘못되었습니다.')
    layout = card.get('layout') or project.get('design', {}).get('layout', 'classic')
    design = project.get('design', {})
    light = layout == 'editorial'
    ink = '#182922' if light else '#f1f4f6'
    muted = '#617468' if light else '#a0adb9'
    rule = '#d4ddcf' if light else '#35404a'
    ink = design.get('text_color') or ink
    background = design.get('background') or ('#f6f4eb' if light else '#111820')
    if design.get('text_color'):
        muted = ink
    image = Image.new('RGB', (1080, 1350), background)
    draw = ImageDraw.Draw(image)
    logo_id = project.get('design', {}).get('logo_id')
    if logo_id:
        path = image_path(logo_id)
        if not path.is_file():
            raise ValueError('로고 파일을 찾을 수 없습니다. 자산 보관함을 확인하세요.')
        with Image.open(path) as logo:
            thumbnail = ImageOps.contain(logo.convert('RGBA'), (240, 80))
            image.paste(thumbnail, (72, 70), thumbnail)
    else:
        draw.rounded_rectangle((72, 78, 275, 132), radius=27, fill=accent)
        draw.text((95, 84), 'MEDIALOG', font=font(28), fill='#111820')
    draw.text((930, 84), f'{index + 1:02d}', font=font(30), fill=accent)
    draw.line((72, 186, 1008, 186), fill=rule, width=2)
    if layout == 'poster':
        heading_y, heading_height, body_y, body_height = 250, 180, 490, 600
        heading_size, body_size = 74, 92
    elif layout == 'image':
        box = (72, 220, 1008, 620)
        if card.get('image_id'):
            path = image_path(card['image_id'])
            if not path.is_file():
                raise ValueError('카드 이미지를 찾을 수 없습니다. 다시 첨부하세요.')
            with Image.open(path) as uploaded:
                image.paste(ImageOps.fit(uploaded.convert('RGB'), (936, 400)), (72, 220))
        else:
            draw.rounded_rectangle(box, radius=18, fill=accent)
            draw.ellipse((520, 250, 920, 590), fill='#294c3b')
            draw.rectangle((200, 310, 610, 470), fill='#111820')
        heading_y, heading_height, body_y, body_height = 660, 155, 840, 270
        heading_size, body_size = 52, 38
    elif light:
        draw.rectangle((72, 235, 84, 435), fill=accent)
        heading_y, heading_height, body_y, body_height = 250, 210, 510, 590
        heading_size, body_size = 60, 50
        draw.rounded_rectangle((56, 480, 1024, 1110), radius=16, fill=background if design.get('background') else '#e8ecdf')
    else:
        heading_y, heading_height, body_y, body_height = 240, 220, 490, 620
        heading_size, body_size = 64, 68 if card.get('role') == 'cover' or index == 0 else 48
    face, lines, spacing = fit(card['heading'], draw, 900 if light else 920, heading_height, heading_size, 24)
    y = heading_y
    for line in lines:
        x = (1080 - draw.textlength(line, font=face)) / 2 if layout == 'poster' else (100 if light else 72)
        draw.text((x, y), line, font=face, fill=ink if light or layout == 'poster' else accent, stroke_width=1 if layout == 'poster' else 0)
        y += spacing
    face, lines, spacing = fit(card['body'], draw, 900, body_height, body_size, 24)
    y = body_y + max(0, (body_height - len(lines) * spacing) / 2) if layout == 'poster' else body_y
    for line in lines:
        x = (1080 - draw.textlength(line, font=face)) / 2 if layout == 'poster' else (88 if light else 72)
        draw.text((x, y), line, font=face, fill=ink, stroke_width=2 if layout == 'poster' else 0)
        y += spacing
    publishers = list(dict.fromkeys(s['publisher'] for s in project['sources'] if s['article_id'] in card['source_ids']))
    footer = '출처: ' + ', '.join(publishers) if publishers else '직접 작성 · 출처 확인 필요'
    if len(footer) > 130:
        footer = footer[:127] + '…'
    face, lines, spacing = fit(footer, draw, 920, 85, 24, 18)
    y = 1160
    draw.line((72, 1130, 1008, 1130), fill=rule, width=2)
    for line in lines:
        draw.text((72, y), line, font=face, fill=muted)
        y += spacing
    draw.text((72, 1260), 'MEDIALOG / EDITORIAL STUDIO', font=font(22), fill=muted)
    if not project['reviewed']:
        draw.text((740, 1260), '검토 전 초안', font=font(24), fill=accent)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
