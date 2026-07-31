"""
Извлечение карт сетевых потоков из базы знаний в плоскую таблицу.

Разделы «Карта сетевых потоков ...» описывают правила межсетевого взаимодействия
таблицами вида «источники | получатели | протокол/порт | описание». Каждая ячейка
содержит списки адресов и портов в свободной форме, поэтому искать по ним
неудобно. Инструмент разворачивает каждое правило в декартово произведение
(адрес источника x адрес получателя x протокол/порт) и складывает результат в
таблицу netflows: одна строка — один разрешённый поток.

Исходный текст ячеек сохраняется рядом с разобранными значениями, поэтому по
таблице можно и фильтровать точно, и читать исходную формулировку правила.

Использование:
    python tool_netflows.py                       # справка
    python tool_netflows.py sections              # какие карты есть в базе знаний
    python tool_netflows.py preview --limit 5     # как разбираются строки (без записи)
    python tool_netflows.py build --truncate      # разобрать и записать в netflows
    python tool_netflows.py stats                 # сводка по таблице
    python tool_netflows.py query --port 445      # поиск потоков
    python tool_netflows.py export --out flows.csv
    python tool_netflows.py issues                # строки, которые не разобрались

Переменные окружения (.env):
    CLICKHOUSE_HOST / PORT / USERNAME / PASSWORD / DATABASE — подключение
    CLICKHOUSE_TABLE     — таблица чанков базы знаний (по умолчанию chunks)
    NETFLOWS_TABLE       — таблица потоков (по умолчанию netflows)
    NETFLOWS_SECTION_PREFIX — по какому началу раздела искать карты
"""

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    """CLI инструмента. Строится до тяжёлых импортов, чтобы --help отвечал сразу."""
    parser = argparse.ArgumentParser(
        description="Карты сетевых потоков из базы знаний -> плоская таблица netflows",
    )
    parser.add_argument("--debug", action="store_true", help="Включить DEBUG-логирование")
    parser.add_argument("--env", metavar="PATH", help="Путь к .env (по умолчанию рядом со скриптом)")

    sub = parser.add_subparsers(dest="command", metavar="command")

    p_sections = sub.add_parser("sections", help="Показать разделы-карты и число строк в них")
    p_sections.add_argument("--source", help="Фильтр по имени файла")

    p_preview = sub.add_parser("preview", help="Показать разбор строк без записи в БД")
    p_preview.add_argument("--limit", type=int, default=5, help="Сколько строк показать (5)")
    p_preview.add_argument("--section", help="Фильтр по названию раздела (подстрока)")
    p_preview.add_argument("--source", help="Фильтр по имени файла")

    p_build = sub.add_parser("build", help="Разобрать карты и записать потоки в таблицу")
    p_build.add_argument("--truncate", action="store_true", help="Очистить таблицу перед записью")
    p_build.add_argument("--dry-run", action="store_true", help="Только посчитать, не писать")
    p_build.add_argument("--section", help="Фильтр по названию раздела (подстрока)")
    p_build.add_argument("--source", help="Фильтр по имени файла")
    p_build.add_argument("--batch-size", type=int, default=10_000, help="Размер пачки вставки (10000)")

    sub.add_parser("stats", help="Сводка по таблице потоков")

    p_query = sub.add_parser("query", help="Поиск потоков по адресам, портам и описанию")
    p_query.add_argument("--src", help="Адрес источника (учитывается вхождение в подсеть)")
    p_query.add_argument("--dst", help="Адрес получателя (учитывается вхождение в подсеть)")
    p_query.add_argument("--port", type=int, help="Порт (попадание в диапазон)")
    p_query.add_argument("--proto", help="Протокол: TCP, UDP, ICMP")
    p_query.add_argument("--map", dest="map_name", help="Карта: КЦОИ-1, КЦОИ-МР, ...")
    p_query.add_argument("--like", help="Подстрока в описании правила или в тексте ячеек")
    p_query.add_argument("--limit", type=int, default=50, help="Сколько строк показать (50)")

    p_export = sub.add_parser("export", help="Выгрузить потоки в CSV")
    p_export.add_argument("--out", default="netflows.csv", help="Файл вывода (netflows.csv)")
    p_export.add_argument("--map", dest="map_name", help="Карта")
    p_export.add_argument("--proto", help="Протокол")

    p_issues = sub.add_parser("issues", help="Строки карт, где не нашлось адресов или портов")
    p_issues.add_argument("--limit", type=int, default=20, help="Сколько строк показать (20)")
    p_issues.add_argument("--kind", choices=["all", "addr", "port"], default="all",
                          help="Что показывать: без адресов, без портов или всё")

    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    _args = _parser.parse_args()
    if not getattr(_args, "command", None):
        _parser.print_help()
        sys.exit(0)


# ---------------------------------------------------------------------------
# Тяжёлые импорты — только если команда задана
# ---------------------------------------------------------------------------

import csv
import json
import logging
import os
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import clickhouse_connect
from clickhouse_connect.driver.client import Client
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    """Подключение к ClickHouse и параметры извлечения карт."""

    clickhouse_host:     str = "localhost"
    clickhouse_port:     int = 8123
    clickhouse_username: str = "clickhouse"
    clickhouse_password: str = "clickhouse"
    clickhouse_database: str = "kcoi"
    clickhouse_table:    str = "chunks"

    netflows_table:          str = "netflows"
    netflows_section_prefix: str = "Карта сетевых потоков"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


# ---------------------------------------------------------------------------
# Модели данных
# ---------------------------------------------------------------------------

class PortSpec(BaseModel):
    """Протокол и диапазон портов из ячейки «протокол/порт»."""

    protocol: str
    port_from: int = 0
    port_to: int = 0
    service: str = ""
    raw: str = ""


class FlowRule(BaseModel):
    """Строка карты сетевых потоков — правило до разворачивания."""

    map_name: str
    source: str
    section: str
    chunk_index: int
    row_no: int
    src_text: str
    dst_text: str
    ports_text: str
    rule_desc: str
    src_addresses: list[str] = Field(default_factory=list)
    dst_addresses: list[str] = Field(default_factory=list)
    ports: list[PortSpec] = Field(default_factory=list)

    @property
    def has_addresses(self) -> bool:
        return bool(self.src_addresses and self.dst_addresses)

    @property
    def flow_count(self) -> int:
        return (len(self.src_addresses) or 1) * (len(self.dst_addresses) or 1) * (len(self.ports) or 1)


class Flow(BaseModel):
    """Развёрнутый поток: один адрес источника, один получателя, один порт."""

    flow_id: str
    map_name: str
    source: str
    section: str
    chunk_index: int
    row_no: int
    src_addr: str
    src_is_cidr: int
    dst_addr: str
    dst_is_cidr: int
    protocol: str
    port_from: int
    port_to: int
    service: str
    port_raw: str
    src_text: str
    dst_text: str
    ports_text: str
    rule_desc: str


# ---------------------------------------------------------------------------
# Разбор ячеек
# ---------------------------------------------------------------------------

_IP_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b")

# Кириллические двойники латиницы: в документах встречается «ТСР/443»
_HOMOGLYPHS = str.maketrans({
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
})

# Частые опечатки в названиях протоколов
_PROTOCOL_FIXES = {"UPD": "UDP", "TPC": "TCP", "TCPP": "TCP"}

_KNOWN_PROTOCOLS = {"TCP", "UDP", "ICMP", "GRE", "ESP", "AH", "IP", "ANY"}

_DASHES = "-–—‒−"

# TCP/445 | TCP (HTTPS)/443 | UDP/49152–65535
_PROTO_PORT_RE = re.compile(
    rf"\b(?P<proto>[A-Za-z]{{2,6}})\s*(?:\((?P<service>[^)]{{1,30}})\))?\s*/\s*"
    rf"(?P<from>\d{{1,5}})(?:\s*[{_DASHES}]\s*(?P<to>\d{{1,5}}))?"
)

# 443/TCP | 22/tcp — обратная запись
_PORT_PROTO_RE = re.compile(
    rf"\b(?P<from>\d{{1,5}})(?:\s*[{_DASHES}]\s*(?P<to>\d{{1,5}}))?\s*/\s*(?P<proto>[A-Za-z]{{2,6}})\b"
)

# TCP 2015 — протокол и порт через пробел, без разделителя
_PROTO_SPACE_PORT_RE = re.compile(
    rf"\b(?P<proto>TCP|UDP)\s+(?P<from>\d{{1,5}})(?:\s*[{_DASHES}]\s*(?P<to>\d{{1,5}}))?\b",
    re.IGNORECASE,
)

# IP/* — любой порт указанного протокола
_PROTO_ANY_PORT_RE = re.compile(r"\b(?P<proto>[A-Za-z]{2,6})\s*/\s*\*")

# ip-proto-105 — номер протокола IP вместо имени
_IP_PROTO_RE = re.compile(r"\bip[\s_-]*proto[\s_-]*(?P<number>\d{1,3})\b", re.IGNORECASE)

# Голое имя протокола без порта: ICMP
_BARE_PROTO_RE = re.compile(r"\b(?P<proto>ICMP|GRE|ESP|AH)\b", re.IGNORECASE)

# Имя сервиса перед портом: «HTTPS – 443/TCP», «SSH – 22/tcp»
_SERVICE_PREFIX_RE = re.compile(rf"\b(?P<service>[A-Za-z][A-Za-z0-9]{{1,14}})\s*[{_DASHES}]\s*(?=\d)")


def _normalize_protocol(raw: str) -> str:
    """Приводит имя протокола к верхнему регистру, чиня опечатки и кириллицу."""
    name = raw.translate(_HOMOGLYPHS).upper()
    return _PROTOCOL_FIXES.get(name, name)


def extract_addresses(text: str) -> list[str]:
    """Все IPv4-адреса и подсети из текста ячейки, в порядке появления, без повторов."""
    seen: dict[str, None] = {}
    for match in _IP_RE.finditer(text.translate(_HOMOGLYPHS)):
        seen.setdefault(match.group(0), None)
    return list(seen)


def parse_ports(text: str) -> list[PortSpec]:
    """Разбирает ячейку «протокол/порт» во все встречающиеся спецификации.

    Поддерживает прямую запись (TCP/445), обратную (443/TCP), диапазоны через
    любое тире, аннотацию сервиса (TCP (HTTPS)/443 и HTTPS – 443/TCP), номера
    протоколов IP (ip-proto-105) и протоколы без портов (ICMP).
    """
    if not text or not text.strip():
        return []

    normalized = text.translate(_HOMOGLYPHS)
    specs: list[PortSpec] = []
    seen: set[tuple[str, int, int]] = set()

    def add(protocol: str, port_from: int, port_to: int, service: str, raw: str) -> None:
        key = (protocol, port_from, port_to)
        if key in seen:
            return
        seen.add(key)
        specs.append(PortSpec(protocol=protocol, port_from=port_from, port_to=port_to,
                              service=service, raw=raw.strip()))

    # Имена сервисов, указанные перед портом, запоминаем по позиции
    services_at: dict[int, str] = {
        match.end(): match.group("service").upper()
        for match in _SERVICE_PREFIX_RE.finditer(normalized)
    }

    consumed: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < c_end and c_start < end for c_start, c_end in consumed)

    for match in _IP_PROTO_RE.finditer(normalized):
        add(f"IP-PROTO-{int(match.group('number'))}", 0, 0, "", match.group(0))
        consumed.append(match.span())

    for match in _PROTO_ANY_PORT_RE.finditer(normalized):
        protocol = _normalize_protocol(match.group("proto"))
        if protocol not in _KNOWN_PROTOCOLS:
            continue
        add(protocol, 0, 65535, "", match.group(0))
        consumed.append(match.span())

    for match in _PROTO_PORT_RE.finditer(normalized):
        if overlaps(*match.span()):
            continue
        protocol = _normalize_protocol(match.group("proto"))
        if protocol not in _KNOWN_PROTOCOLS:
            continue
        port_from = int(match.group("from"))
        port_to = int(match.group("to") or port_from)
        add(protocol, port_from, port_to, (match.group("service") or "").upper(), match.group(0))
        consumed.append(match.span())

    for match in _PORT_PROTO_RE.finditer(normalized):
        if overlaps(*match.span()):
            continue
        protocol = _normalize_protocol(match.group("proto"))
        if protocol not in _KNOWN_PROTOCOLS:
            continue
        port_from = int(match.group("from"))
        port_to = int(match.group("to") or port_from)
        add(protocol, port_from, port_to, services_at.get(match.start(), ""), match.group(0))
        consumed.append(match.span())

    for match in _PROTO_SPACE_PORT_RE.finditer(normalized):
        if overlaps(*match.span()):
            continue
        port_from = int(match.group("from"))
        port_to = int(match.group("to") or port_from)
        add(_normalize_protocol(match.group("proto")), port_from, port_to, "", match.group(0))
        consumed.append(match.span())

    for match in _BARE_PROTO_RE.finditer(normalized):
        if overlaps(*match.span()):
            continue
        add(_normalize_protocol(match.group("proto")), 0, 0, "", match.group(0))
        consumed.append(match.span())

    return specs


def extract_map_name(section: str) -> str:
    """Короткое имя карты из названия раздела.

    «Карта сетевых потоков в КЦОИ-1» -> «КЦОИ-1»;
    «Карта сетевых потоков стенда функционального тестирования в КЦОИ-1» -> «КЦОИ-1-стенд».
    Стенд выделяется отдельно: смешивать тестовые потоки с продуктивными в одной
    карте нельзя, это разные наборы правил межсетевого экрана.
    """
    head = section.split(">")[0].strip()
    suffix = "-стенд" if re.search(r"стенд", head, re.IGNORECASE) else ""

    match = re.search(r"(КЦОИ[-\s]?\S+)", head)
    if match:
        return match.group(1).strip(" .,").replace(" ", "-") + suffix

    prefix = "Карта сетевых потоков"
    return (head[len(prefix):].strip(" вдля") or head) + suffix


# ---------------------------------------------------------------------------
# Чтение карт из базы знаний
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = 4


def read_flow_rules(
    client: Client,
    settings: Settings,
    section_filter: Optional[str] = None,
    source_filter: Optional[str] = None,
) -> Iterator[FlowRule]:
    """Читает строки карт сетевых потоков и разбирает их в правила.

    Из строки берутся последние четыре значимые ячейки: в части таблиц слева есть
    лишняя колонка от объединённой шапки, дублирующая источник, а справа
    встречаются пустые ячейки — без их отбрасывания разбор съезжает на колонку.
    Строки-категории, где все ячейки совпадают, пропускаются.
    """
    where = [
        "chunk_type = 'table_row'",
        "positionCaseInsensitiveUTF8(section, {prefix:String}) > 0",
    ]
    params: dict = {"prefix": settings.netflows_section_prefix}
    if section_filter:
        where.append("positionCaseInsensitiveUTF8(section, {sec:String}) > 0")
        params["sec"] = section_filter
    if source_filter:
        where.append("positionCaseInsensitiveUTF8(source, {src:String}) > 0")
        params["src"] = source_filter

    sql = f"""
        SELECT source, section, content, chunk_index
        FROM {settings.clickhouse_database}.{settings.clickhouse_table} FINAL
        WHERE {' AND '.join(where)}
        ORDER BY source, section, chunk_index
    """

    row_no = 0
    for source, section, content, chunk_index in client.query(sql, parameters=params).result_rows:
        try:
            cells = [str(cell) for cell in json.loads(content)]
        except (json.JSONDecodeError, TypeError):
            logger.debug(f"пропуск нечитаемого чанка [{source}] {section[:40]} #{chunk_index}")
            continue

        while len(cells) > _EXPECTED_COLUMNS and not cells[-1].strip():
            cells.pop()

        if len(cells) < _EXPECTED_COLUMNS:
            continue
        if len(set(cell.strip() for cell in cells)) == 1:
            continue  # строка-категория на всю ширину таблицы

        src_text, dst_text, ports_text, rule_desc = cells[-_EXPECTED_COLUMNS:]
        row_no += 1
        yield FlowRule(
            map_name=extract_map_name(section),
            source=source,
            section=section,
            chunk_index=int(chunk_index),
            row_no=row_no,
            src_text=src_text,
            dst_text=dst_text,
            ports_text=ports_text,
            rule_desc=rule_desc,
            src_addresses=extract_addresses(src_text),
            dst_addresses=extract_addresses(dst_text),
            ports=parse_ports(ports_text),
        )


def expand_rule(rule: FlowRule) -> Iterator[Flow]:
    """Разворачивает правило в потоки: источник x получатель x протокол/порт.

    Если адреса или порты в ячейке не распознаны, подставляется пустое значение —
    правило всё равно попадает в таблицу вместе с исходным текстом, иначе оно
    просто потерялось бы.
    """
    sources = rule.src_addresses or [""]
    destinations = rule.dst_addresses or [""]
    ports = rule.ports or [PortSpec(protocol="", raw="")]

    for src_addr in sources:
        for dst_addr in destinations:
            for port in ports:
                key = "|".join([
                    rule.map_name, rule.source, rule.section, str(rule.chunk_index),
                    src_addr, dst_addr, port.protocol, str(port.port_from), str(port.port_to),
                ])
                yield Flow(
                    flow_id=str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                    map_name=rule.map_name,
                    source=rule.source,
                    section=rule.section,
                    chunk_index=rule.chunk_index,
                    row_no=rule.row_no,
                    src_addr=src_addr,
                    src_is_cidr=int("/" in src_addr),
                    dst_addr=dst_addr,
                    dst_is_cidr=int("/" in dst_addr),
                    protocol=port.protocol,
                    port_from=port.port_from,
                    port_to=port.port_to,
                    service=port.service,
                    port_raw=port.raw,
                    src_text=rule.src_text,
                    dst_text=rule.dst_text,
                    ports_text=rule.ports_text,
                    rule_desc=rule.rule_desc,
                )


# ---------------------------------------------------------------------------
# Таблица потоков
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {database}.{table}
(
    flow_id      UUID,
    map_name     LowCardinality(String),
    source       String,
    section      String,
    chunk_index  UInt32,
    row_no       UInt32,
    src_addr     String,
    src_is_cidr  UInt8,
    dst_addr     String,
    dst_is_cidr  UInt8,
    protocol     LowCardinality(String),
    port_from    UInt32,
    port_to      UInt32,
    service      LowCardinality(String),
    port_raw     String,
    src_text     String,
    dst_text     String,
    ports_text   String,
    rule_desc    String,
    imported_at  DateTime
)
ENGINE = ReplacingMergeTree(imported_at)
ORDER BY (map_name, src_addr, dst_addr, protocol, port_from, port_to, flow_id)
"""

_COLUMNS = [
    "flow_id", "map_name", "source", "section", "chunk_index", "row_no",
    "src_addr", "src_is_cidr", "dst_addr", "dst_is_cidr",
    "protocol", "port_from", "port_to", "service", "port_raw",
    "src_text", "dst_text", "ports_text", "rule_desc", "imported_at",
]


def make_client(settings: Settings) -> Client:
    """Подключение к ClickHouse."""
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_username,
        password=settings.clickhouse_password,
    )


def ensure_table(client: Client, settings: Settings) -> None:
    """Создаёт таблицу потоков, если её ещё нет."""
    client.command(f"CREATE DATABASE IF NOT EXISTS {settings.clickhouse_database}")
    client.command(_CREATE_TABLE_SQL.format(
        database=settings.clickhouse_database, table=settings.netflows_table))


def insert_flows(client: Client, settings: Settings, flows: list[Flow], imported_at: datetime) -> None:
    """Вставляет пачку потоков."""
    if not flows:
        return
    rows = [
        [
            flow.flow_id, flow.map_name, flow.source, flow.section, flow.chunk_index, flow.row_no,
            flow.src_addr, flow.src_is_cidr, flow.dst_addr, flow.dst_is_cidr,
            flow.protocol, flow.port_from, flow.port_to, flow.service, flow.port_raw,
            flow.src_text, flow.dst_text, flow.ports_text, flow.rule_desc, imported_at,
        ]
        for flow in flows
    ]
    client.insert(
        f"{settings.clickhouse_database}.{settings.netflows_table}",
        rows, column_names=_COLUMNS,
    )


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

def cmd_sections(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Список разделов-карт с числом строк."""
    where = ["chunk_type = 'table_row'", "positionCaseInsensitiveUTF8(section, {prefix:String}) > 0"]
    params: dict = {"prefix": settings.netflows_section_prefix}
    if args.source:
        where.append("positionCaseInsensitiveUTF8(source, {src:String}) > 0")
        params["src"] = args.source

    sql = f"""
        SELECT source, section, count() AS rows
        FROM {settings.clickhouse_database}.{settings.clickhouse_table} FINAL
        WHERE {' AND '.join(where)}
        GROUP BY source, section ORDER BY rows DESC
    """
    rows = client.query(sql, parameters=params).result_rows
    print(f"\nРазделов с картами: {len(rows)}, строк всего: {sum(r[2] for r in rows)}\n")
    for source, section, count in rows:
        print(f"  {count:5d}  [{extract_map_name(section):10}] {section[:78]}")
        print(f"         {source}")
    print()


def cmd_preview(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Показывает, как разбираются строки, без записи в базу."""
    shown = 0
    for rule in read_flow_rules(client, settings, args.section, args.source):
        if shown >= args.limit:
            break
        shown += 1
        print("=" * 100)
        print(f"[{rule.map_name}] {rule.section[:80]}")
        print(f"  источники ({len(rule.src_addresses)}): {', '.join(rule.src_addresses) or '— не найдено —'}")
        print(f"     текст: {rule.src_text[:110]}")
        print(f"  получатели ({len(rule.dst_addresses)}): {', '.join(rule.dst_addresses) or '— не найдено —'}")
        print(f"     текст: {rule.dst_text[:110]}")
        ports = ", ".join(
            f"{p.protocol}/{p.port_from}" + (f"-{p.port_to}" if p.port_to != p.port_from else "")
            + (f" ({p.service})" if p.service else "")
            for p in rule.ports
        )
        print(f"  порты ({len(rule.ports)}): {ports or '— не найдено —'}")
        print(f"     текст: {rule.ports_text[:110]}")
        print(f"  описание: {rule.rule_desc[:110]}")
        print(f"  -> потоков из строки: {rule.flow_count}")
    print()


def cmd_build(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Разбирает карты и записывает потоки в таблицу."""
    if not args.dry_run:
        ensure_table(client, settings)
        if args.truncate:
            client.command(f"TRUNCATE TABLE IF EXISTS "
                           f"{settings.clickhouse_database}.{settings.netflows_table}")
            logger.info(f"Таблица {settings.netflows_table} очищена")

    imported_at = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    batch: list[Flow] = []
    rules = flows_total = written = 0
    no_addr = no_ports = 0
    protocols: Counter = Counter()

    for rule in read_flow_rules(client, settings, args.section, args.source):
        rules += 1
        if not rule.has_addresses:
            no_addr += 1
        if not rule.ports:
            no_ports += 1
        for flow in expand_rule(rule):
            flows_total += 1
            protocols[flow.protocol or "(нет)"] += 1
            if args.dry_run:
                continue
            batch.append(flow)
            if len(batch) >= args.batch_size:
                insert_flows(client, settings, batch, imported_at)
                written += len(batch)
                logger.info(f"  записано {written} потоков")
                batch = []

    if batch and not args.dry_run:
        insert_flows(client, settings, batch, imported_at)
        written += len(batch)

    print(f"\nРазобрано строк карт:      {rules}")
    print(f"  без адресов:             {no_addr}")
    print(f"  без распознанных портов: {no_ports}")
    print(f"Потоков получено:          {flows_total}")
    print(f"Записано в {settings.netflows_table}: {'0 (dry-run)' if args.dry_run else written}")
    print(f"Протоколы: {', '.join(f'{p}={n}' for p, n in protocols.most_common(10))}\n")


def cmd_stats(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Сводка по таблице потоков."""
    table = f"{settings.clickhouse_database}.{settings.netflows_table}"
    if not client.command(f"EXISTS TABLE {table}"):
        print(f"\nТаблица {table} не найдена. Сначала выполните: build\n")
        return

    total = client.command(f"SELECT count() FROM {table} FINAL")
    print(f"\nВсего потоков: {total}")
    if not total:
        print()
        return

    print(f"Уникальных адресов источников: "
          f"{client.command(f'SELECT uniq(src_addr) FROM {table} FINAL')}")
    print(f"Уникальных адресов получателей: "
          f"{client.command(f'SELECT uniq(dst_addr) FROM {table} FINAL')}")

    print("\nПо картам:")
    for map_name, count, rules in client.query(
        f"SELECT map_name, count(), uniq(section, row_no) FROM {table} FINAL "
        f"GROUP BY map_name ORDER BY count() DESC"
    ).result_rows:
        print(f"  {map_name:12} {count:8d} потоков из {rules} правил")

    print("\nПо протоколам:")
    for protocol, count in client.query(
        f"SELECT protocol, count() FROM {table} FINAL GROUP BY protocol ORDER BY count() DESC"
    ).result_rows:
        print(f"  {protocol or '(нет)':12} {count:8d}")

    print("\nТоп-15 портов:")
    for port, count in client.query(
        f"SELECT port_from, count() FROM {table} FINAL WHERE port_from > 0 "
        f"GROUP BY port_from ORDER BY count() DESC LIMIT 15"
    ).result_rows:
        print(f"  {port:8d} {count:8d}")
    print()


def cmd_query(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Поиск потоков по адресам, портам, протоколу и описанию."""
    table = f"{settings.clickhouse_database}.{settings.netflows_table}"
    where: list[str] = []
    params: dict = {"lim": args.limit}

    # Адрес ищется и точным совпадением, и вхождением в записанную подсеть
    if args.src:
        where.append("(src_addr = {src:String} OR (src_is_cidr = 1 AND "
                     "isIPAddressInRange({src:String}, src_addr)))")
        params["src"] = args.src
    if args.dst:
        where.append("(dst_addr = {dst:String} OR (dst_is_cidr = 1 AND "
                     "isIPAddressInRange({dst:String}, dst_addr)))")
        params["dst"] = args.dst
    if args.port is not None:
        where.append("(port_from <= {port:UInt32} AND port_to >= {port:UInt32})")
        params["port"] = args.port
    if args.proto:
        where.append("upper(protocol) = upper({proto:String})")
        params["proto"] = args.proto
    if args.map_name:
        where.append("positionCaseInsensitiveUTF8(map_name, {map:String}) > 0")
        params["map"] = args.map_name
    if args.like:
        where.append("(positionCaseInsensitiveUTF8(rule_desc, {like:String}) > 0 "
                     "OR positionCaseInsensitiveUTF8(src_text, {like:String}) > 0 "
                     "OR positionCaseInsensitiveUTF8(dst_text, {like:String}) > 0)")
        params["like"] = args.like

    condition = " AND ".join(where) if where else "1"
    total = client.query(
        f"SELECT count() FROM {table} FINAL WHERE {condition}", parameters=params
    ).result_rows[0][0]

    rows = client.query(
        f"SELECT src_addr, dst_addr, protocol, port_from, port_to, map_name, rule_desc "
        f"FROM {table} FINAL WHERE {condition} "
        f"ORDER BY map_name, src_addr, dst_addr, port_from LIMIT {{lim:UInt32}}",
        parameters=params,
    ).result_rows

    print(f"\nНайдено потоков: {total}, показано: {len(rows)}\n")
    print(f"  {'источник':22} {'получатель':22} {'порт':16} {'карта':10} описание")
    for src, dst, proto, pfrom, pto, map_name, desc in rows:
        port = f"{proto}/{pfrom}" + (f"-{pto}" if pto != pfrom else "") if proto else "—"
        print(f"  {src:22} {dst:22} {port:16} {map_name:10} {desc[:60]}")
    print()


def cmd_export(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Выгружает потоки в CSV."""
    table = f"{settings.clickhouse_database}.{settings.netflows_table}"
    where: list[str] = []
    params: dict = {}
    if args.map_name:
        where.append("positionCaseInsensitiveUTF8(map_name, {map:String}) > 0")
        params["map"] = args.map_name
    if args.proto:
        where.append("upper(protocol) = upper({proto:String})")
        params["proto"] = args.proto
    condition = " AND ".join(where) if where else "1"

    columns = ["map_name", "src_addr", "dst_addr", "protocol", "port_from", "port_to",
               "service", "rule_desc", "source", "section"]
    rows = client.query(
        f"SELECT {', '.join(columns)} FROM {table} FINAL WHERE {condition} "
        f"ORDER BY map_name, src_addr, dst_addr, port_from",
        parameters=params,
    ).result_rows

    out_path = Path(args.out)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(columns)
        writer.writerows(rows)

    print(f"\nВыгружено {len(rows)} потоков в {out_path.resolve()}\n")


def cmd_issues(args: argparse.Namespace, settings: Settings, client: Client) -> None:
    """Строки карт, где не нашлось адресов или портов, — для ручной проверки."""
    shown = 0
    counters = Counter()
    for rule in read_flow_rules(client, settings):
        problems = []
        if not rule.src_addresses:
            problems.append("нет адресов источника")
        if not rule.dst_addresses:
            problems.append("нет адресов получателя")
        if not rule.ports:
            problems.append("не распознаны порты")
        if not problems:
            continue

        counters["адреса"] += int(not rule.has_addresses)
        counters["порты"] += int(not rule.ports)
        if args.kind == "addr" and rule.has_addresses:
            continue
        if args.kind == "port" and rule.ports:
            continue
        if shown >= args.limit:
            continue
        shown += 1
        print("=" * 100)
        print(f"[{rule.map_name}] {rule.section[:80]} (строка {rule.row_no})")
        print(f"  проблемы: {'; '.join(problems)}")
        print(f"  источник : {rule.src_text[:100]}")
        print(f"  получатель: {rule.dst_text[:100]}")
        print(f"  порты    : {rule.ports_text[:100]}")
        print(f"  описание : {rule.rule_desc[:100]}")

    print(f"\nИтого строк без адресов: {counters['адреса']}, без портов: {counters['порты']}\n")


_COMMANDS = {
    "sections": cmd_sections,
    "preview":  cmd_preview,
    "build":    cmd_build,
    "stats":    cmd_stats,
    "query":    cmd_query,
    "export":   cmd_export,
    "issues":   cmd_issues,
}


def main(args: argparse.Namespace) -> None:
    """Точка входа: окружение, логирование, выполнение команды."""
    env_path = Path(args.env) if args.env else Path(__file__).with_name(".env")
    load_dotenv(env_path)
    if args.debug:
        os.environ["LOG_LEVEL"] = "DEBUG"

    from logging_config import setup_logging
    setup_logging("tool_netflows")

    settings = Settings()
    logger.info(
        f"ClickHouse: {settings.clickhouse_host}:{settings.clickhouse_port} -> "
        f"{settings.clickhouse_database} (чанки: {settings.clickhouse_table}, "
        f"потоки: {settings.netflows_table})"
    )

    client = make_client(settings)
    try:
        _COMMANDS[args.command](args, settings, client)
    finally:
        client.close()


if __name__ == "__main__":
    main(_args)
