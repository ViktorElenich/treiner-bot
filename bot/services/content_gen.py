"""Подготовка дайджестов свежих исследований для Telegram-группы."""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree

import aiohttp
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Модель можно сменить через env GEMINI_MODEL без правки кода
# (запасной вариант, если preview-модель отключат: gemini-2.5-flash-lite)
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")


def _make_client(api_key: str) -> "genai.Client":
    """
    Клиент Gemini. Если задан env GEMINI_BASE_URL — запросы идут через
    прокси (Cloudflare Worker): Google блокирует IP дата-центров Render
    (403 Forbidden), а IP Cloudflare — нет.
    """
    base_url = os.environ.get("GEMINI_BASE_URL", "").strip().rstrip("/")
    if base_url:
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(base_url=base_url),
        )
    return genai.Client(api_key=api_key)

# Ошибки Gemini, которые лечатся повтором через паузу.
# «User location is not supported» — Google иногда неверно определяет
# страну по IP серверов Render; ошибка плавающая, повтор обычно проходит.
_TRANSIENT_MARKERS = (
    "user location is not supported",
    "failed_precondition",
    "resource_exhausted",
    "unavailable",
    "overloaded",
    "deadline",
    "timeout",
    "429",
    "500",
    "503",
)


def _is_transient(error: Exception) -> bool:
    msg = str(error).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


async def _call_gemini(api_key: str, contents, attempts: int = 4) -> str:
    """
    Вызов Gemini с повторами при временных ошибках.
    Паузы между попытками: 5, 15, 45 сек.
    Возвращает текст ответа, при неудаче бросает последнюю ошибку.
    """
    delay = 5
    for attempt in range(1, attempts + 1):
        try:
            client = _make_client(api_key)
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=MODEL,
                contents=contents,
            )
            return response.text.strip()
        except Exception as e:
            if attempt == attempts or not _is_transient(e):
                raise
            logger.warning(
                "Gemini временная ошибка (попытка %d/%d), повтор через %d сек: %s",
                attempt, attempts, delay, e,
            )
            await asyncio.sleep(delay)
            delay *= 3


# ── Свежие исследования из PubMed ───────────────────────────────

# PubMed — бесплатная научная база NCBI. Берём только исследования с
# аннотацией и сильными типами дизайна: РКИ, систематические обзоры и
# метаанализы. Так бот не выдаёт «посты из головы» под видом науки.
PUBMED_EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PUBMED_SEARCH_QUERIES = {
    "nutrition": (
        '((nutrition[Title/Abstract] OR diet[Title/Abstract] '
        'OR protein[Title/Abstract] OR "energy intake"[Title/Abstract]) '
        'AND humans[MeSH Terms] '
        'AND ("systematic review"[Publication Type] '
        'OR "meta-analysis"[Publication Type] '
        'OR "randomized controlled trial"[Publication Type]))'
    ),
    "article": (
        '((exercise[Title/Abstract] OR "resistance training"[Title/Abstract] '
        'OR "physical activity"[Title/Abstract] OR training[Title/Abstract]) '
        'AND humans[MeSH Terms] '
        'AND ("systematic review"[Publication Type] '
        'OR "meta-analysis"[Publication Type] '
        'OR "randomized controlled trial"[Publication Type]))'
    ),
}


@dataclass(frozen=True)
class ResearchPaper:
    """Минимальные проверяемые данные статьи из PubMed."""

    pmid: str
    title: str
    abstract: str
    journal: str
    published: str
    publication_types: tuple[str, ...]
    doi: str = ""

    @property
    def pubmed_url(self) -> str:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/"


def _element_text(element: Optional[ElementTree.Element]) -> str:
    """Возвращает текст XML-элемента, включая вложенные теги форматирования."""
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _format_publication_date(article: ElementTree.Element) -> str:
    date_node = (
        article.find("./MedlineCitation/Article/ArticleDate")
        or article.find("./MedlineCitation/Article/Journal/JournalIssue/PubDate")
    )
    if date_node is None:
        return ""
    year = date_node.findtext("Year", default="")
    month = date_node.findtext("Month", default="")
    return " ".join(part for part in (month, year) if part)


def _parse_pubmed_articles(xml: str) -> list[ResearchPaper]:
    """Разбирает ответ EFetch и оставляет статьи с достаточно полным abstract."""
    root = ElementTree.fromstring(xml)
    papers = []
    for article in root.findall("./PubmedArticle"):
        pmid = article.findtext("./MedlineCitation/PMID", default="").strip()
        title = _element_text(article.find("./MedlineCitation/Article/ArticleTitle"))
        abstract_parts = [
            _element_text(part)
            for part in article.findall("./MedlineCitation/Article/Abstract/AbstractText")
        ]
        abstract = " ".join(part for part in abstract_parts if part)[:5000]
        if not pmid or not title or len(abstract) < 250:
            continue

        publication_types = tuple(
            _element_text(item)
            for item in article.findall(
                "./MedlineCitation/Article/PublicationTypeList/PublicationType"
            )
            if _element_text(item)
        )
        doi = ""
        for identifier in article.findall("./PubmedData/ArticleIdList/ArticleId"):
            if identifier.attrib.get("IdType") == "doi":
                doi = _element_text(identifier)
                break

        papers.append(
            ResearchPaper(
                pmid=pmid,
                title=title,
                abstract=abstract,
                journal=_element_text(article.find("./MedlineCitation/Article/Journal/Title")),
                published=_format_publication_date(article),
                publication_types=publication_types,
                doi=doi,
            )
        )
    return papers


async def _fetch_recent_research(
    content_type: str,
    excluded_pmids: set[str],
) -> list[ResearchPaper]:
    """Находит свежие статьи PubMed и возвращает до двух ещё не использованных."""
    query = PUBMED_SEARCH_QUERIES[content_type]
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": "200",
        "sort": "pub date",
        "retmode": "json",
        "datetype": "pdat",
        "reldate": "365",  # только публикации за последний год
        "tool": "viktor_treiner_bot",
    }
    ncbi_email = os.environ.get("NCBI_EMAIL", "").strip()
    if ncbi_email:
        params["email"] = ncbi_email

    timeout = aiohttp.ClientTimeout(total=25)
    headers = {"User-Agent": "viktor-treiner-bot/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(f"{PUBMED_EUTILS_URL}/esearch.fcgi", params=params) as response:
            response.raise_for_status()
            found = await response.json()
        pmids = found.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        fetch_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "tool": "viktor_treiner_bot",
        }
        if ncbi_email:
            fetch_params["email"] = ncbi_email
        async with session.get(
            f"{PUBMED_EUTILS_URL}/efetch.fcgi", params=fetch_params
        ) as response:
            response.raise_for_status()
            xml = await response.text()

    available = [paper for paper in _parse_pubmed_articles(xml) if paper.pmid not in excluded_pmids]
    return available[:2]


def _research_prompt(
    content_type: str,
    papers: list[ResearchPaper],
    past_titles: list[str],
) -> str:
    topic = "питании" if content_type == "nutrition" else "тренировках"
    sources = []
    for number, paper in enumerate(papers, start=1):
        publication_types = ", ".join(paper.publication_types) or "не указан"
        sources.append(
            f"""ИССЛЕДОВАНИЕ {number}
Название: {paper.title}
Журнал: {paper.journal or "не указан"}
Дата публикации: {paper.published or "не указана"}
Тип публикации: {publication_types}
PMID: {paper.pmid}
Аннотация: {paper.abstract}"""
        )

    previous = ", ".join(past_titles[:20])
    previous_hint = (
        f"Не повторяй по смыслу уже опубликованные выпуски: {previous}."
        if previous else ""
    )
    return f"""Ты готовишь русскоязычный дайджест свежих научных публикаций о {topic} для Telegram-чата фитнес-тренера.

Тебе даны записи PubMed ниже. Используй ТОЛЬКО сведения из этих записей.
Нельзя придумывать цифры, результаты, участников, причинно-следственные выводы,
ссылки или «практические рекомендации», которых нет в аннотации.

Задача: ясно и по делу объяснить, что именно изучали, что показали результаты и
как это можно осторожно учитывать в тренировочном процессе или питании. Укажи
тип исследования человеческим языком: например, метаанализ, систематический обзор
или рандомизированное исследование. Если данные ограничены, противоречивы или
относятся к узкой группе людей — скажи об этом. Не ставь диагнозы, не назначай
лечение, не используй сенсационные формулировки и не выдавай один результат за
универсальное правило.

Пиши простым русским языком, без вступлений ради вступления, рекламы и эмодзи.
Объём основного текста: 220–350 слов. Не добавляй раздел «Источники» — он будет
вставлен ботом автоматически. Не используй Markdown-разметку.

Формат ответа:
первая строка — короткий заголовок (3–8 слов, без точки),
пустая строка,
затем основной текст с подзаголовками «Что изучали», «Что получилось»,
«Что это значит на практике» и «Ограничения».

{previous_hint}

{chr(10).join(sources)}"""


def _append_sources(text: str, papers: list[ResearchPaper]) -> str:
    """Добавляет проверяемые прямые ссылки независимо от ответа модели."""
    lines = [text.strip(), "", "Источники:"]
    for number, paper in enumerate(papers, start=1):
        title = paper.title[:180].rstrip()
        lines.append(f"{number}. {title} — {paper.pubmed_url}")
        if paper.doi:
            lines.append(f"DOI: https://doi.org/{paper.doi}")
    return "\n".join(lines)


DICTATION_PROMPT = """Ты — редактор фитнес-тренера Виктора. Он надиктовал черновик поста для своего Telegram-чата{topic_hint}.

Преобразуй надиктовку в готовый пост:
- Убери слова-паразиты, оговорки, случайные повторы, обрывки фраз
- Разбей на абзацы, поправь грамматику
- СОХРАНИ манеру и формулировки Виктора: его связки («то есть», «соответственно», «допустим», «смотри»), обращение на «ты», порядок мыслей
- НИЧЕГО не добавляй от себя — никаких фактов, советов и выводов, которых нет в надиктовке
- НЕ используй Markdown-разметку (**, ##, списки с * или -)
- Эмодзи: максимум 1-2, только если уместно
- На русском языке

Формат ответа:
Первая строка — короткий заголовок темы (3-6 слов, без точки в конце)
Пустая строка
Далее — текст поста"""


def _parse_title_and_text(raw: str) -> tuple:
    """Первая строка — заголовок, дальше — текст поста."""
    lines = raw.split("\n", 2)
    title = lines[0].strip()
    text = lines[2].strip() if len(lines) > 2 else raw
    return title, text


async def generate_content(
    content_type: str,
    api_key: str,
    past_titles: Optional[list] = None,
    excluded_pmids: Optional[set[str]] = None,
) -> tuple:
    """
    Готовит дайджест свежих исследований PubMed через Gemini API.

    content_type: "nutrition" или "article"
    past_titles: заголовки предыдущих выпусков для исключения повторов
    Возвращает (title, text, papers) или ("", "❌ Ошибка...", []).
    """
    try:
        from bot.database import get_used_research_pmids

        used_pmids = await get_used_research_pmids()
        used_pmids.update(excluded_pmids or set())
        papers = await _fetch_recent_research(content_type, used_pmids)
        if not papers:
            return "", (
                "Не нашёл подходящих новых публикаций PubMed за последний год. "
                "Лучше пропустить выпуск и попробовать позже."
            ), []

        prompt = _research_prompt(content_type, papers, past_titles or [])
        raw = await _call_gemini(api_key, prompt)
        title, text = _parse_title_and_text(raw)
        text = _append_sources(text, papers)
        logger.info(
            "Дайджест исследований подготовлен: type=%s, title=%r, pmids=%s",
            content_type, title, ",".join(paper.pmid for paper in papers),
        )
        return title, text, papers

    except Exception as e:
        logger.error("Ошибка подготовки дайджеста исследований: %s", e)
        return "", f"❌ Ошибка подготовки дайджеста: {e}", []


async def structure_dictation(
    api_key: str,
    text: Optional[str] = None,
    audio: Optional[bytes] = None,
    audio_mime: str = "audio/ogg",
    topic: Optional[str] = None,
) -> tuple:
    """
    Превращает надиктовку тренера (голосовое или текст) в оформленный пост.

    text: надиктовка текстом, ИЛИ
    audio: байты голосового сообщения (Gemini расшифровывает сам)
    topic: тема-подсказка (не обязательно)
    Возвращает (title, text) или ("", "❌ Ошибка...") при ошибке.
    """
    topic_hint = f" на тему «{topic}»" if topic else ""
    prompt = DICTATION_PROMPT.format(topic_hint=topic_hint)

    contents = [prompt]
    if audio:
        contents.append(types.Part.from_bytes(data=audio, mime_type=audio_mime))
    else:
        contents.append(f"Надиктовка (текстом):\n{text}")

    try:
        raw = await _call_gemini(api_key, contents)
        title, post_text = _parse_title_and_text(raw)
        logger.info("Надиктовка оформлена: title=%r, length=%d", title, len(post_text))
        return title, post_text

    except Exception as e:
        logger.error("Ошибка оформления надиктовки: %s", e)
        return "", f"❌ Ошибка оформления: {e}"
