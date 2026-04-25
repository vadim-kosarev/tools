"""Вывод случайных/найденных чанков из ClickHouse с метаданными.

Usage:
    python _peek_chroma.py --random                          # 15 случайных чанков
    python _peek_chroma.py --random --type table_row         # фильтр по chunk_type
    python _peek_chroma.py --random --type table_row --limit 30
    python _peek_chroma.py --random --type "" --limit 10     # chunk_type="" (обычный текст)
    python _peek_chroma.py --search "текст запроса"          # семантический поиск (векторный)
    python _peek_chroma.py --exact "точная фраза"            # поиск по точному вхождению
    python _peek_chroma.py --search "запрос" --type table_row --limit 5  # комбинация
"""
import sys
import json
import argparse

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from chroma_utils import get_store, make_embeddings
from rag_chat import settings
from langchain_core.documents import Document

sep = "=" * 70


def _print_doc(idx: int, doc: Document, distance: float | None = None) -> None:
    m = doc.metadata
    chunk_type = m.get("chunk_type", "")
    print(f"\n{sep}")
    header = f"[{idx}]"
    if distance is not None:
        header += f"  distance: {distance:.4f}"
    print(header)
    print(f"  source:      {m.get('source', '')}")
    print(f"  section:     {m.get('section', '')[:300]}")
    print(f"  chunk_type:  {chunk_type}")

    # table_headers: stored as JSON array ["h1","h2"] — display as comma-separated
    tbl_raw = m.get("table_headers", "")
    if tbl_raw:
        try:
            headers = json.loads(tbl_raw)
            tbl_display = " | ".join(headers)
        except (json.JSONDecodeError, TypeError):
            tbl_display = tbl_raw
        print(f"  table_hdr:   {tbl_display[:330]}")

    # table_row content: stored as JSON array of cell values — display as "h: v | h: v"
    if chunk_type == "table_row" and tbl_raw:
        try:
            headers = json.loads(tbl_raw)
            cells = json.loads(doc.page_content)
            kv = " | ".join(f"{h}: {v}" for h, v in zip(headers, cells) if h or v)
            print(f"  Content ({len(doc.page_content)} chars): {kv[:300]}")
        except (json.JSONDecodeError, TypeError):
            content = doc.page_content[:300].replace("\n", " | ")
            print(f"  Content ({len(doc.page_content)} chars): {content}")
    else:
        content = doc.page_content[:300].replace("\n", " | ")
        print(f"  Content ({len(doc.page_content)} chars): {content}")

parser = argparse.ArgumentParser(
    description="Peek chunks from ClickHouse vector store",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="""
Examples:
  python _peek_chroma.py --random                          # 15 random chunks
  python _peek_chroma.py --random --type table_row         # filter by chunk_type
  python _peek_chroma.py --random --limit 30               # 30 random chunks
  python _peek_chroma.py --random --type "" --limit 10     # plain-text chunks only
  python _peek_chroma.py --search "текст запроса"          # semantic vector search
  python _peek_chroma.py --exact "точная фраза"            # exact substring search
  python _peek_chroma.py --search "запрос" --type table_row --limit 5
""",
)
parser.add_argument("--random", "-r", action="store_true",
                    help="Show a random sample of chunks")
parser.add_argument("--type", dest="chunk_type", default=None,
                    help="Filter by chunk_type (use '' for plain text chunks)")
parser.add_argument("--limit", "-n", type=int, default=15,
                    help="Number of chunks to display (default: 15)")
parser.add_argument("--search", "-s", default=None,
                    help="Semantic (vector) search query")
parser.add_argument("--exact", "-e", default=None,
                    help="Exact substring search in document content")
args = parser.parse_args()

# Show help when no action is specified
if not args.random and not args.search and not args.exact:
    parser.print_help()
    sys.exit(0)

store = get_store()
total = store.count()
print(f"Всего чанков в ClickHouse "
      f"({settings.clickhouse_database}.{settings.clickhouse_table}): {total}")

sep = "=" * 70

if args.search:
    print(f"Семантический поиск: '{args.search}'")
    embeddings = make_embeddings()
    query_vec = embeddings.embed_query(args.search)
    results = store.similarity_search_by_vector_with_score(
        query_vec, k=args.limit,
        chunk_type=args.chunk_type if args.chunk_type is not None else None,
    )
    for i, (doc, distance) in enumerate(results, 1):
        _print_doc(i, doc, distance=distance)

elif args.exact:
    print(f"Точный поиск: '{args.exact}'")
    docs = store.exact_search(args.exact, limit=args.limit, chunk_type=args.chunk_type)
    print(f"Найдено: {len(docs)}")
    for i, doc in enumerate(docs, 1):
        _print_doc(i, doc)

else:
    # --random
    docs = store.get_sample(limit=args.limit, chunk_type=args.chunk_type)
    if args.chunk_type is not None:
        print(f"Фильтр chunk_type='{args.chunk_type}', показано: {len(docs)}")
    for i, doc in enumerate(docs, 1):
        _print_doc(i, doc)

print(sep)
