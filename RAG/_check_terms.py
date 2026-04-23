import chromadb

c = chromadb.HttpClient(host="192.168.1.99", port=3266)
col = c.get_collection("ec-leasing")
print("Total docs:", col.count())

for term in ["СОИБ", "RDEFINE", "RACF", "z/OS", "НШР", "КОИБ", "ресурс"]:
    r = col.get(where_document={"$contains": term}, limit=2, include=["documents"])
    print(f"{term}: {len(r['documents'])} results")
    if r["documents"]:
        print("  Sample:", r["documents"][0][:200])

