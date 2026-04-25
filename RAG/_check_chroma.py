import sys
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()
from chroma_utils import get_collection

col = get_collection()
print("Total docs:", col.count())

for term in ["RDEFINE", "rdefine", "DEFINE", "R DEFINE"]:
    r = col.get(where_document={"$contains": term}, limit=3, include=["documents"])
    print(f"{term}: {len(r['documents'])} results")
    if r["documents"]:
        print("  Sample:", r["documents"][0][:300])
