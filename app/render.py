import io
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
from .assets import image_path
from .headlines import format_card_body

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
        for word in paragraph.split():
            candidate = f'{line} {word}' if line else word
            if draw.textlength(candidate, font=face) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ''
            # Split only a single word wider than the entire card.
            for char in word:
                if line and draw.textlength(line + char, font=face) > width:
                    lines.append(line)
                    line = ''
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



def bold_font(size):
    path = Path(__file__).resolve().parent.parent / 'fonts' / 'NotoSansCJKkr-Bold.otf'
    return ImageFont.truetype(str(path), size) if path.is_file() else font(size)


def fit_cover(text, draw):
    text=' '.join(text.split())
    face = bold_font(112)
    lines = wrap(text, draw, face, 930)
    if len(lines) <= 2:
        return face, lines, int(112 * 1.38)
    for size in range(100, 87, -2):
        face = bold_font(size)
        lines = wrap(text, draw, face, 930)
        if len(lines) <= 3:
            return face, lines, int(size * 1.38)
    raise ValueError('표지 제목은 세 줄 이내로 짧게 작성해 주세요.')


def centered_title_origin(draw, text, face, top):
    left, upper, right, _ = draw.textbbox((0, 0), text, font=face)
    return (1080 - (right - left)) / 2 - left, top - upper


def full_photo_background(source, size):
    """Preserve the complete photo; use a blurred backdrop for ratio differences."""
    photo = ImageOps.exif_transpose(source).convert('RGB')
    backdrop = ImageOps.fit(photo, size).filter(ImageFilter.GaussianBlur(36))
    complete = ImageOps.contain(photo, size)
    backdrop.paste(complete, ((size[0] - complete.width) // 2,
                              (size[1] - complete.height) // 2))
    return backdrop


def render_breaking(project, index):
    card = project['cards'][index]
    design = project.get('design', {})
    background = design.get('background') or '#191b1d'
    ink = design.get('text_color') or '#ffffff'
    accent = project['accent']
    image = Image.new('RGB', (1080, 1350), background)
    if card.get('image_id'):
        path = image_path(card['image_id'])
        if not path.is_file():
            raise ValueError('배경 사진 파일을 찾을 수 없습니다. 자산 보관함을 확인하세요.')
        with Image.open(path) as source:
            image = full_photo_background(source, image.size)
        image = Image.blend(image, Image.new('RGB', image.size, '#000000'), .62)
    draw = ImageDraw.Draw(image)
    cover = card.get('role') == 'cover' or index == 0
    if cover:
        if design.get('badge_id'):
            with Image.open(image_path(design['badge_id'])) as banner:
                thumb = ImageOps.contain(banner.convert('RGBA'), (350, 88))
                image.paste(thumb, ((1080-thumb.width)//2, 180), thumb)
        else:
            draw.polygon([(428, 180), (673, 180), (654, 240), (409, 240)], fill=accent)
            draw.polygon([(414, 180), (422, 180), (402, 240), (394, 240)], fill=accent)
            draw.text((540, 208), '뉴스속보', anchor='mm', font=bold_font(36), fill='#ffffff')
    body = card['body'] or '원문을 확인하고 내용을 작성하세요.'
    if not cover:
        body = format_card_body(body)
    max_size = 112 if cover else 48
    paragraphs = [body] if cover else body.split('\n\n')
    if cover:
        face,lines,spacing=fit_cover(body,draw)
        blocks=[lines];total_height=len(lines)*spacing;gap=0
    for size in ([] if cover else range(max_size,19,-2)):
        face = bold_font(size) if cover else font(size)
        blocks = [wrap(paragraph, draw, face, 930 if cover else 880) for paragraph in paragraphs]
        spacing = int(size * (1.38 if cover else 1.65))
        gap = 0 if cover else int(size * .65)
        total_height = sum(len(lines) * spacing for lines in blocks) + gap * (len(blocks)-1)
        if total_height <= (670 if cover else 800):
            break
    else:
        if not cover:raise ValueError('속보 카드 문구가 너무 깁니다. 문구를 줄여 주세요.')
    y = 320 if cover else 240
    for lines in blocks:
        for line in lines:
            if cover:
                x, baseline = centered_title_origin(draw, line, face, y)
                draw.text((x + 3, baseline + 5), line, font=face, fill='#000000', stroke_width=3)
                draw.text((x, baseline), line, font=face, fill=ink)
            else:
                draw.text((100, y), line, font=face, fill=ink)
            y += spacing
        y += gap
    if cover:
        publishers = list(dict.fromkeys(source['publisher'] for source in project['sources'] if source['article_id'] in card['source_ids']))
        footer = '출처: ' + ', '.join(publishers) if publishers else '직접 작성 · 출처 확인 필요'
        face, lines, spacing = fit(footer[:130], draw, 920, 80, 22, 18)
        for number, line in enumerate(lines):
            draw.text((540, 1110 + number * spacing), line, anchor='mt', font=face, fill=ink)
    logo_id = design.get('logo_id')
    if logo_id:
        with Image.open(image_path(logo_id)) as logo:
            thumb = ImageOps.contain(logo.convert('RGBA'), (300, 80))
            image.paste(thumb, ((1080 - thumb.width)//2, 1190), thumb)
    else:
        # Colored wordmark, separate from any news photograph.
        face = bold_font(58)
        name = '미디어로그'
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).text((540, 1190), name, anchor='mt', font=face, fill=255)
        gradient = Image.new('RGB', image.size)
        gradient_draw = ImageDraw.Draw(gradient)
        for x in range(1080):
            amount = max(0, min(1, (x - 360) / 360))
            gradient_draw.line((x, 1190, x, 1280), fill=(int(15 + 215*amount), int(75*(1-amount)), int(235*(1-amount)+35*amount)))
        image.paste(gradient, (0, 0), mask)
    if card.get('ai_background'):
        draw.text((1008, 58), 'AI 생성 이미지', anchor='rt', font=font(20), fill=ink)
    draw.text((980, 1300), f'{index+1:02d}', anchor='rt', font=font(20), fill=ink)
    if not project['reviewed']:
        draw.text((70, 1300), '검토 전 초안', font=font(20), fill=ink)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()

def render_card(project, index):
    card = project['cards'][index]
    accent = project['accent']
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent):
        raise ValueError('강조색 형식이 잘못되었습니다.')
    layout = card.get('layout') or project.get('design', {}).get('layout', 'classic')
    if layout == 'breaking':
        return render_breaking(project, index)
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
