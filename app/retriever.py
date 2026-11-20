"""Semantic search over project descriptions + DarGlobal pages.

Chunks are embedded with a small local ONNX model (fastembed, no PyTorch).
The index is built once (`python -m app.retriever`) and loaded at startup.
"""
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "processed"
INDEX = ROOT / "data" / "index"
MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _chunks(text, size=1100, overlap=150):
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        cut = text.rfind("\n", start + size // 2, end)  # prefer breaking at a line
        end = cut if cut > 0 and end < len(text) else end
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return out


def build_chunks():
    chunks = []
    for l in (DATA / "listings.jsonl").open():
        r = json.loads(l)
        head = (f"{r['title']} — {r.get('property_type') or ''} in {r.get('city') or ''}, "
                f"{r.get('country') or ''} (source: {r['source']})")
        body = r.get("full_text") or r.get("description") or ""
        for c in _chunks(body):
            chunks.append({"text": f"{head}\n{c}", "title": r["title"], "url": r["url"],
                           "listing_id": r["id"], "source": r["source"]})
    for l in (DATA / "pages.jsonl").open():
        p = json.loads(l)
        for c in _chunks(p["text"]):
            chunks.append({"text": f"{p['title']} (DarGlobal {p['kind']})\n{c}", "title": p["title"],
                           "url": p["url"], "listing_id": None, "source": "darglobal"})
    return chunks


def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL, cache_dir=str(ROOT / "data" / "models"))


def build_index():
    chunks = build_chunks()
    vecs = np.array(list(_model().embed([c["text"] for c in chunks])), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    INDEX.mkdir(parents=True, exist_ok=True)
    np.save(INDEX / "vectors.npy", vecs)
    (INDEX / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False))
    print(f"indexed {len(chunks)} chunks -> {INDEX}")


class Retriever:
    def __init__(self):
        self.vecs = np.load(INDEX / "vectors.npy")
        self.chunks = json.loads((INDEX / "chunks.json").read_text())
        self.model = _model()

    def search(self, query, k=6, source=None):
        q = np.array(next(iter(self.model.embed([QUERY_PREFIX + query]))), dtype=np.float32)
        q /= np.linalg.norm(q)
        scores = self.vecs @ q
        out, seen = [], {}
        for i in np.argsort(-scores):
            c = self.chunks[i]
            if source and c["source"] != source:
                continue
            if seen.get(c["url"], 0) >= 2:  # max 2 chunks per page for diversity
                continue
            seen[c["url"]] = seen.get(c["url"], 0) + 1
            out.append({"score": round(float(scores[i]), 3), "title": c["title"],
                        "url": c["url"], "listing_id": c["listing_id"], "text": c["text"]})
            if len(out) >= k:
                break
        return out


if __name__ == "__main__":
    build_index()
