from typing import Literal
from pydantic import BaseModel, Field, model_validator
from . import rss

Layout = Literal['classic', 'editorial', 'image']
Role = Literal['cover', 'facts', 'background', 'impact', 'sources', 'custom']


class Reference(BaseModel):
    url: str = Field(max_length=2000)
    note: str = Field(default='', max_length=300)

    @model_validator(mode='after')
    def valid_url(self):
        self.url = rss.canonical_url(self.url)
        return self


class Design(BaseModel):
    layout: Layout = 'classic'
    accent: str = Field(default='#c5f66a', pattern=r'^#[0-9a-fA-F]{6}$')
    notes: str = Field(default='', max_length=2000)
    references: list[Reference] = Field(default_factory=list, max_length=10)
    image_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def valid_ids(self):
        import re
        if any(not re.fullmatch(r'[a-f0-9]{32}', id) for id in self.image_ids):
            raise ValueError('이미지 ID가 올바르지 않습니다.')
        return self


class Outline(BaseModel):
    role: Role = 'facts'
    heading: str = Field(min_length=1, max_length=100)


def default_outline():
    return [Outline(role=role, heading=heading) for role, heading in [
        ('cover', '오늘의 이슈'), ('facts', '핵심 내용'), ('background', '배경과 맥락'),
        ('facts', '관련 보도'), ('impact', '더 알아볼 내용'), ('sources', '출처 · 원문 확인')]]
