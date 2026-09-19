"""Подготовка дайджестов свежих исследований для Telegram-группы."""

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from datetime import date, timedelta
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


# ── Свежие исследования: PubMed + Europe PMC ────────────────────

# Два бесплатных научных источника. PubMed (NCBI) — основной,
# Europe PMC — запасной: он индексирует часть журналов, которых в PubMed нет.
# Берём только исследования с аннотацией и сильными типами дизайна:
# РКИ, систематические обзоры и метаанализы.
PUBMED_EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPEPMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Окно поиска: публикации за последние два года
SEARCH_WINDOW_DAYS = 730


@dataclass(frozen=True)
class Topic:
    """Узкая тема выпуска. Из неё строятся запросы к обоим источникам."""

    label: str                      # как тема называется для тренера
    terms: tuple[str, ...]          # ключевые фразы темы
    context: tuple[str, ...] = ()   # уточнение (обязательно должно встретиться)


# Темы чередуются по дням, поэтому одна и та же возвращается примерно
# раз в месяц. Фразы берём точные («dietary protein», а не «protein»):
# одиночное слово protein цепляет медицинские работы про белки крови.
TOPICS: dict[str, tuple[Topic, ...]] = {
    "nutrition": (
        Topic(
            "белок и мышцы",
            ("dietary protein", "protein intake", "whey protein", "protein supplementation"),
            ("muscle", "lean mass", "strength", "body composition"),
        ),
        Topic(
            "дефицит калорий и снижение веса",
            ("energy restriction", "caloric restriction", "energy deficit", "weight loss diet"),
            ("body weight", "fat mass", "energy intake"),
        ),
        Topic(
            "интервальное голодание и режим питания",
            ("intermittent fasting", "time-restricted eating", "time restricted feeding", "meal timing"),
        ),
        Topic(
            "углеводы и сахар",
            ("dietary carbohydrate", "low carbohydrate diet", "added sugar", "glycemic index"),
        ),
        Topic(
            "креатин и спортивные добавки",
            ("creatine supplementation", "beta-alanine", "caffeine supplementation", "ergogenic aid"),
        ),
        Topic(
            "витамины и микронутриенты",
            ("vitamin D supplementation", "omega-3 supplementation", "magnesium supplementation", "iron supplementation"),
        ),
        Topic(
            "вода и обезвоживание",
            ("hydration status", "fluid intake", "dehydration"),
            ("exercise", "performance", "physical activity"),
        ),
        Topic(
            "клетчатка, овощи и пищеварение",
            ("dietary fiber", "dietary fibre", "whole grain", "fruit and vegetable intake"),
        ),
        Topic(
            "аппетит, тяга к еде и переедание",
            ("appetite", "satiety", "food craving", "eating behaviour", "eating behavior"),
        ),
        Topic(
            "кофе, алкоголь и сладкие напитки",
            ("coffee consumption", "alcohol consumption", "sugar-sweetened beverages", "energy drinks"),
        ),
        Topic(
            "средиземноморская и растительная диета",
            ("mediterranean diet", "plant-based diet", "vegetarian diet", "vegan diet"),
        ),
        Topic(
            "питание, сахар крови и обмен веществ",
            ("diet", "dietary intervention", "dietary pattern", "nutrition intervention"),
            ("insulin sensitivity", "blood glucose", "glycemic control", "metabolic syndrome", "cholesterol"),
        ),
        Topic(
            "питание и сон",
            ("diet", "dietary intake", "nutrition"),
            ("sleep quality", "sleep duration", "insomnia"),
        ),
    ),
    "article": (
        Topic(
            "силовые тренировки и рост мышц",
            ("resistance training", "strength training"),
            ("hypertrophy", "muscle mass", "muscle strength"),
        ),
        Topic(
            "объём, частота и отдых между подходами",
            ("training volume", "training frequency", "rest interval", "repetitions in reserve", "training to failure"),
        ),
        Topic(
            "кардио и выносливость",
            ("aerobic exercise", "endurance training", "cardiorespiratory fitness"),
        ),
        Topic(
            "интервальные тренировки",
            ("high-intensity interval training", "sprint interval training"),
        ),
        Topic(
            "восстановление и боль в мышцах",
            ("delayed onset muscle soreness", "muscle soreness", "muscle damage", "recovery strategies"),
            ("exercise", "training", "resistance exercise"),
        ),
        Topic(
            "сон и тренировки",
            ("sleep quality", "sleep duration", "sleep deprivation"),
            ("exercise", "physical activity", "athletes", "training"),
        ),
        Topic(
            "разминка, растяжка и подвижность",
            ("stretching", "warm-up", "flexibility", "range of motion"),
            ("exercise", "performance", "training"),
        ),
        Topic(
            "боль в спине и суставах",
            ("low back pain", "knee osteoarthritis", "shoulder pain", "neck pain"),
            ("exercise therapy", "exercise", "resistance training"),
        ),
        Topic(
            "профилактика травм",
            ("injury prevention", "hamstring injury", "sports injury"),
            ("exercise", "training programme", "training program"),
        ),
        Topic(
            "тренировки и снижение веса",
            ("exercise training", "physical activity"),
            ("fat mass", "body composition", "weight loss", "abdominal fat"),
        ),
        Topic(
            "женское здоровье и тренировки",
            ("menstrual cycle", "menopause", "postpartum", "pregnancy"),
            ("exercise", "resistance training", "physical activity"),
        ),
        Topic(
            "тренировки после 50 и саркопения",
            ("sarcopenia", "older adults", "aging muscle"),
            ("resistance training", "exercise", "physical activity"),
        ),
        Topic(
            "тренировки, стресс и настроение",
            ("depression", "anxiety", "mental health", "perceived stress"),
            ("exercise", "physical activity", "resistance training"),
        ),
        Topic(
            "ходьба и повседневная активность",
            ("step count", "walking intervention", "sedentary behaviour", "sedentary behavior"),
        ),
    ),
}


def _topic_for_day(content_type: str, shift: int = 0) -> Topic:
    """
    Тема дня. Рубрика выходит через день, поэтому делим номер дня на 2 —
    так темы идут по кругу и одна и та же повторяется примерно раз в месяц.
    """
    topics = TOPICS[content_type]
    day = date.today().toordinal()
    return topics[((day // 2) + shift) % len(topics)]


@dataclass(frozen=True)
class ResearchPaper:
    """Минимальные проверяемые данные статьи из научной базы."""

    pmid: str                       # ключ дедупликации: PMID либо id Europe PMC
    title: str
    abstract: str
    journal: str
    published: str
    publication_types: tuple[str, ...]
    doi: str = ""
    url: str = ""                   # прямая ссылка на статью
    topic: str = ""                 # тема выпуска, в рамках которой найдена
    source: str = "PubMed"


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


def _parse_pubmed_articles(xml: str, topic_label: str = "") -> list[ResearchPaper]:
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
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                topic=topic_label,
                source="PubMed",
            )
        )
    return papers


_STRONG_DESIGNS = (
    '"systematic review"[Publication Type] '
    'OR "meta-analysis"[Publication Type] '
    'OR "randomized controlled trial"[Publication Type]'
)

# Узкоклинические темы: для чата тренера бесполезны и вытесняют нужное —
# таких работ в базе очень много, и они лезут в любую выборку.
_EXCLUDED_CONTEXTS = (
    "cancer", "chemotherapy", "tumor", "tumour", "dialysis", "transplantation",
    "intensive care", "critical illness", "mechanical ventilation", "palliative",
    "stroke", "spinal cord injury", "cerebral palsy", "schizophrenia",
    "multiple sclerosis", "cystic fibrosis", "COVID-19", "HIV",
)


def _pubmed_query(topic: Topic, strict: bool) -> str:
    """
    Запрос PubMed по теме.

    strict=True — ключевая фраза темы должна быть в НАЗВАНИИ статьи.
    Так отсекаются работы, где тема упомянута вскользь в аннотации.
    """
    field = "Title" if strict else "Title/Abstract"
    terms = " OR ".join(f'"{term}"[{field}]' for term in topic.terms)
    query = f"({terms})"
    if topic.context:
        context = " OR ".join(f'"{item}"[Title/Abstract]' for item in topic.context)
        query += f" AND ({context})"
    excluded = " OR ".join(f'"{item}"[Title]' for item in _EXCLUDED_CONTEXTS)
    query += (
        " AND humans[MeSH Terms] AND hasabstract AND English[Language]"
        f" AND ({_STRONG_DESIGNS})"
        f" NOT ({excluded})"
    )
    return query


def _europepmc_query(topic: Topic, strict: bool) -> str:
    """Тот же смысл, но синтаксисом Europe PMC."""
    if strict:
        terms = " OR ".join(f'TITLE:"{term}"' for term in topic.terms)
    else:
        terms = " OR ".join(f'(TITLE:"{term}" OR ABSTRACT:"{term}")' for term in topic.terms)
    query = f"({terms})"
    if topic.context:
        context = " OR ".join(f'(TITLE:"{item}" OR ABSTRACT:"{item}")' for item in topic.context)
        query += f" AND ({context})"
    excluded = " OR ".join(f'TITLE:"{item}"' for item in _EXCLUDED_CONTEXTS)
    since = (date.today() - timedelta(days=SEARCH_WINDOW_DAYS)).isoformat()
    query += (
        ' AND (PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Meta-Analysis"'
        ' OR PUB_TYPE:"Systematic Review")'
        " AND HAS_ABSTRACT:Y AND LANG:eng"
        f" AND (FIRST_PDATE:[{since} TO {date.today().isoformat()}])"
        f" NOT ({excluded})"
    )
    return query


async def _search_pubmed(
    session: aiohttp.ClientSession,
    topic: Topic,
    excluded: set[str],
    strict: bool = True,
) -> list[ResearchPaper]:
    """Свежие статьи PubMed по теме, кроме уже использованных."""
    params = {
        "db": "pubmed",
        "term": _pubmed_query(topic, strict),
        "retmax": "60",
        "sort": "pub date",
        "retmode": "json",
        "datetype": "pdat",
        "reldate": str(SEARCH_WINDOW_DAYS),
        "tool": "viktor_treiner_bot",
    }
    ncbi_email = os.environ.get("NCBI_EMAIL", "").strip()
    if ncbi_email:
        params["email"] = ncbi_email

    async with session.get(f"{PUBMED_EUTILS_URL}/esearch.fcgi", params=params) as response:
        response.raise_for_status()
        found = await response.json()
    pmids = [pmid for pmid in found.get("esearchresult", {}).get("idlist", []) if pmid not in excluded]
    if not pmids:
        return []

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmids[:40]),
        "retmode": "xml",
        "tool": "viktor_treiner_bot",
    }
    if ncbi_email:
        fetch_params["email"] = ncbi_email
    async with session.get(f"{PUBMED_EUTILS_URL}/efetch.fcgi", params=fetch_params) as response:
        response.raise_for_status()
        xml = await response.text()

    return [
        paper for paper in _parse_pubmed_articles(xml, topic.label)
        if paper.pmid not in excluded
    ]


async def _search_europepmc(
    session: aiohttp.ClientSession,
    topic: Topic,
    excluded: set[str],
    strict: bool = True,
) -> list[ResearchPaper]:
    """Запасной источник: Europe PMC. Препринты не берём — они без рецензии."""
    params = {
        "query": _europepmc_query(topic, strict),
        "format": "json",
        "resultType": "core",
        "pageSize": "40",
        "sort": "P_PDATE_D desc",
    }
    async with session.get(EUROPEPMC_URL, params=params) as response:
        response.raise_for_status()
        found = await response.json()

    papers = []
    for item in found.get("resultList", {}).get("result", []):
        source = item.get("source", "")
        if source == "PPR":  # препринт
            continue
        abstract = " ".join((item.get("abstractText") or "").split())[:5000]
        title = " ".join((item.get("title") or "").split())
        pmid = (item.get("pmid") or "").strip()
        article_id = (item.get("id") or "").strip()
        key = pmid or f"{source}:{article_id}"
        if not title or len(abstract) < 250 or not key or key in excluded:
            continue

        publication_types = tuple(
            str(value) for value in (item.get("pubTypeList") or {}).get("pubType", []) if value
        )
        url = (
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            if pmid else f"https://europepmc.org/article/{source}/{article_id}"
        )
        papers.append(
            ResearchPaper(
                pmid=key,
                title=title,
                abstract=abstract,
                journal=(item.get("journalTitle") or "").strip(),
                published=(item.get("firstPublicationDate") or "").strip(),
                publication_types=publication_types,
                doi=(item.get("doi") or "").strip(),
                url=url,
                topic=topic.label,
                source="Europe PMC",
            )
        )
    return papers


def _pick_papers(candidates: list[ResearchPaper]) -> list[ResearchPaper]:
    """
    Берём две статьи из десятка самых свежих, а не всегда две верхние —
    иначе выпуски по одной теме получаются почти одинаковыми.
    """
    pool = candidates[:10]
    if len(pool) <= 2:
        return pool
    return random.sample(pool, 2)


async def _fetch_recent_research(
    content_type: str,
    excluded_pmids: set[str],
) -> list[ResearchPaper]:
    """
    Подбирает статьи по теме дня. Если по теме ничего нового нет,
    переходит к следующей теме — так утренний выпуск не срывается.
    """
    timeout = aiohttp.ClientTimeout(total=25)
    headers = {"User-Agent": "viktor-treiner-bot/1.0"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        # Сначала строгий поиск (тема в названии) по нескольким темам подряд,
        # и только если совсем пусто — мягкий (тема в аннотации).
        for strict in (True, False):
            for shift in range(4):
                topic = _topic_for_day(content_type, shift)
                try:
                    candidates = await _search_pubmed(session, topic, excluded_pmids, strict)
                except Exception as e:
                    logger.warning("PubMed не ответил по теме %r: %s", topic.label, e)
                    candidates = []

                if len(candidates) < 2:
                    seen = excluded_pmids | {paper.pmid for paper in candidates}
                    try:
                        candidates += await _search_europepmc(session, topic, seen, strict)
                    except Exception as e:
                        logger.warning("Europe PMC не ответил по теме %r: %s", topic.label, e)

                if candidates:
                    papers = _pick_papers(candidates)
                    logger.info(
                        "Тема выпуска: %r (%s), кандидатов: %d, строгий поиск: %s",
                        topic.label, content_type, len(candidates), strict,
                    )
                    return papers
                logger.info("По теме %r новых статей нет — беру следующую", topic.label)

    return []


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
    topic_label = papers[0].topic if papers and papers[0].topic else ""
    topic_hint = (
        f"Тема этого выпуска: {topic_label}. Держись её и не уходи в сторону."
        if topic_label else ""
    )
    return f"""Ты готовишь русскоязычный дайджест свежих научных публикаций о {topic} для Telegram-чата фитнес-тренера.

{topic_hint}

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
        lines.append(f"{number}. {title} — {paper.url}")
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
                "Не нашёл новых подходящих исследований ни в PubMed, ни в Europe PMC "
                "(проверил несколько тем). Лучше пропустить выпуск и попробовать позже."
            ), []

        prompt = _research_prompt(content_type, papers, past_titles or [])
        raw = await _call_gemini(api_key, prompt)
        title, text = _parse_title_and_text(raw)
        text = _append_sources(text, papers)

        # Помечаем статьи использованными сразу: даже если выпуск не опубликуют,
        # завтра бот возьмёт другие — иначе дайджесты повторяются день в день.
        try:
            from bot.database import save_research_sources

            await save_research_sources(
                content_type, [(paper.pmid, paper.title) for paper in papers]
            )
        except Exception as e:
            logger.warning("Не удалось запомнить использованные статьи: %s", e)

        logger.info(
            "Дайджест исследований подготовлен: type=%s, тема=%r, title=%r, ids=%s",
            content_type, papers[0].topic, title,
            ",".join(paper.pmid for paper in papers),
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
