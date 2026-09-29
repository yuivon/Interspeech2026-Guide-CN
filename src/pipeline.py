#!/usr/bin/env python3
"""ISCA Interspeech 2026 官方元数据采集、分析与静态站点构建。"""
import argparse
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CACHE = ROOT / "cache"
LOGS = ROOT / "logs"
PROCEEDINGS = "https://www.isca-archive.org/interspeech_2026/index.html"
FIELDS = ["研究动机", "解决问题", "现象分析", "主要方法", "数据与实验", "主要贡献"]
ARRAY_FIELDS = ["topics", "tasks", "method_families", "specific_methods", "models", "representations", "datasets", "metrics", "languages", "modalities", "application_domains", "contribution_types"]
ENTITY_FIELDS = {
    "dataset_entities": ["name", "role", "new_or_existing", "language", "domain", "modality", "scale_text", "availability", "collection_method", "annotation_method", "evidence"],
    "evaluation_entities": ["name", "type", "task", "automatic_or_human", "evidence"],
}
EVIDENCE_TYPES = ["numerical_result", "dataset", "metric", "experiment_setup", "key_finding", "method", "model", "language"]
PAPER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": ["paper_id", "analysis", "structured", *ENTITY_FIELDS, "special_tags", "evidence"], "additionalProperties": False,
    "properties": {
        "paper_id": {"type": "string", "minLength": 1},
        "analysis_source": {"enum": ["full_text", "abstract"]},
        "source_isca_page": {"type": "string"},
        "source_pdf": {"type": "string"},
        "source_hash": {"type": "string"},
        "analysis": {"type": "object", "required": FIELDS, "additionalProperties": False, "properties": {k: {"type": "string", "minLength": 1} for k in FIELDS}},
        "structured": {"type": "object", "required": ARRAY_FIELDS, "additionalProperties": False, "properties": {k: {"type": "array", "items": {"type": "string"}} for k in ARRAY_FIELDS}},
        **{key: {"type": "array", "items": {"type": "object", "required": fields, "additionalProperties": False, "properties": {field: ({"type": "array", "items": {"type": "string"}} if field in ("language", "modality") else {"type": "string", **({"enum": ["new", "expanded", "reannotated", "curated", "existing", "unknown"]} if field == "new_or_existing" else {"enum": ["public", "restricted", "not_stated", "unknown"]} if field == "availability" else {"enum": ["proposed", "used", "benchmark", "expanded", "reannotated", "curated", "systematic_study", "unknown"]} if field == "role" and key == "dataset_entities" else {"enum": ["benchmark", "metric", "protocol", "human", "robustness", "reliability", "diagnostic", "other"]} if field == "type" else {"enum": ["automatic", "human", "both", "unknown"]} if field == "automatic_or_human" else {})}) for field in fields}}} for key, fields in ENTITY_FIELDS.items()},
        "special_tags": {"type": "object", "required": ["dataset", "evaluation"], "additionalProperties": False, "properties": {k: {"type": "object", "required": ["relevant", "reason", "confidence"], "additionalProperties": False, "properties": {"relevant": {"type": "boolean"}, "reason": {"type": "string"}, "confidence": {"enum": ["high", "medium", "low"]}}} for k in ("dataset", "evaluation")}},
        "evidence": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["claim_type", "field", "evidence_text", "page_or_section", "source"], "additionalProperties": False, "properties": {"claim_type": {"enum": EVIDENCE_TYPES}, "field": {"type": "string", "minLength": 1}, "evidence_text": {"type": "string", "minLength": 1}, "page_or_section": {"type": "string"}, "source": {"enum": ["full_text", "abstract"]}}}},
    },
}

def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def get(url, timeout=45):
    req = Request(url, headers={"User-Agent": "Interspeech2026GuideCN/1.0 (polite academic metadata crawler)"})
    with urlopen(req, timeout=timeout) as r:
        return r.read(), r.status, r.headers.get("Content-Type", "")

class ProceedingsParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.session = ""; self.in_h4 = False; self.in_link = False
        self.link = ""; self.text = []; self.items = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h4": self.in_h4 = True; self.heading = []
        if tag == "a" and a.get("href", "").lower().endswith("_interspeech.html"):
            self.in_link = True; self.link = a["href"]; self.text = []
    def handle_data(self, data):
        if self.in_h4: self.heading.append(data)
        if self.in_link: self.text.append(data)
    def handle_endtag(self, tag):
        if tag == "h4":
            self.session = " ".join("".join(self.heading).split()); self.in_h4 = False
        if tag == "a" and self.in_link:
            bits = [x.strip() for x in "\n".join(self.text).splitlines() if x.strip()]
            if bits and self.session and not re.match(r"^keynote(?:\s*\d|\b)", self.session, re.I):
                self.items.append((self.session, self.link, bits[0], bits[-1] if len(bits) > 1 else ""))
            self.in_link = False

class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.meta = {}; self.in_abstract = False; self.abstract_depth = 0; self.abstract = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("name", "").startswith("citation_"):
            self.meta.setdefault(a["name"], []).append(a.get("content", ""))
        if tag == "div" and a.get("id") == "abstract": self.in_abstract = True; self.abstract_depth = 1
        elif self.in_abstract and tag == "div": self.abstract_depth += 1
        if self.in_abstract and tag in ("br", "p"): self.abstract.append(" ")
    def handle_data(self, data):
        if self.in_abstract: self.abstract.append(data)
    def handle_endtag(self, tag):
        if self.in_abstract and tag == "div":
            self.abstract_depth -= 1
            if self.abstract_depth == 0: self.in_abstract = False

def paper_id(url):
    return Path(urlparse(url).path).stem

def crawl(limit=None):
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "details").mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    body, _, _ = get(PROCEEDINGS)
    (RAW / "proceedings.html").write_bytes(body)
    parser = ProceedingsParser(); parser.feed(body.decode("utf-8", "replace"))
    seen = set(); entries = []
    for session, href, title, authors in parser.items:
        page = urljoin(PROCEEDINGS, href); pid = paper_id(page)
        if pid in seen: continue
        seen.add(pid)
        entries.append({"paper_id": pid, "title": html.unescape(title), "authors": html.unescape(authors), "original_session": session, "normalized_session": session, "isca_page_url": page, "conference": "Interspeech 2026", "year": 2026})
    listed_count = len(entries)
    listed_sessions = sorted({session for session, _, _, _ in parser.items})
    if limit: entries = entries[:limit]
    errors = []
    def detail(p):
        try:
            detail_path = RAW / "details" / f'{p["paper_id"]}.html'
            if detail_path.exists(): raw = detail_path.read_bytes(); status = 200
            else:
                raw, status, _ = get(p["isca_page_url"])
                detail_path.write_bytes(raw)
            d = DetailParser(); d.feed(raw.decode("utf-8", "replace")); m = d.meta
            p.update(title=m.get("citation_title", [p["title"]])[0] or p["title"], authors=m.get("citation_author", []) or p["authors"].split(", "), abstract=" ".join(" ".join(d.abstract).split()), pdf_url=m.get("citation_pdf_url", [""])[0], doi=m.get("citation_doi", [""])[0], pages="-".join(x for x in [m.get("citation_firstpage", [""])[0], m.get("citation_lastpage", [""])[0]] if x), source_isca_page=p["isca_page_url"], detail_http_status=status)
            if not p["abstract"]: raise ValueError("ISCA abstract missing")
            return p
        except Exception as e: return {"error": str(e), "paper": p}
    workers = max(1, min(4, int(os.getenv("MAX_WORKERS", "2"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(detail, p) for p in entries]
        papers = []
        for f in as_completed(futures):
            row = f.result()
            if "error" in row: errors.append({"paper_id": row["paper"]["paper_id"], "title": row["paper"]["title"], "reason": row["error"]})
            else: papers.append(row)
    papers.sort(key=lambda p: p["paper_id"])
    write_json(DATA / "papers.json", papers); write_json(LOGS / "crawl_errors.json", errors)
    # Keep the raw count even if a detail page fails so validation can detect loss.
    write_json(RAW / "crawl_manifest.json", {"proceedings_url": PROCEEDINGS, "listed_count": listed_count, "listed_sessions": listed_sessions, "sample_limit": limit, "parsed_detail_count": len(papers), "errors": len(errors)})
    return papers

def normalize_sessions(papers):
    aliases_path = DATA / "category_aliases.json"; aliases = read_json(aliases_path, {})
    groups = {}
    session_names = read_json(RAW / "crawl_manifest.json", {}).get("listed_sessions", [p["original_session"] for p in papers])
    for session in session_names:
        m = re.match(r"^(.*?)(?:\s+(?:Part\s+)?(\d+|I|II|III|IV|V))$", session, re.I)
        if m: groups.setdefault(m.group(1).strip(), set()).add(m.group(2).upper())
    for base, numbers in groups.items():
        if len(numbers) > 1:
            for session in session_names:
                if re.match(rf"^{re.escape(base)}\s+(?:Part\s+)?(?:\d+|I|II|III|IV|V)$", session, re.I): aliases.setdefault(session, base)
    manifest_path = RAW / "crawl_manifest.json"
    manifest = read_json(manifest_path, {})
    manifest["normalized_sessions"] = sorted({aliases.get(session, session) for session in session_names})
    manifest["session_alias_count"] = len(aliases)
    manifest["session_normalization_pass"] = all(aliases.get(session) == base for base, numbers in groups.items() if len(numbers) > 1 for session in session_names if re.match(rf"^{re.escape(base)}\s+(?:Part\s+)?(?:\d+|I|II|III|IV|V)$", session, re.I))
    write_json(manifest_path, manifest)
    write_json(aliases_path, aliases)
    for p in papers: p["normalized_session"] = aliases.get(p["original_session"], p["original_session"])
    write_json(DATA / "papers.json", papers)
    return papers

def fetch_pdfs(papers, limit=None):
    pdfdir = CACHE / "pdfs"; pdfdir.mkdir(parents=True, exist_ok=True); errors = []
    selected = papers[:limit] if limit else papers
    def one(p):
        path = pdfdir / f'{p["paper_id"]}.pdf'
        if path.exists() and path.stat().st_size > 1000: return (p["paper_id"], True, "cached")
        if not p.get("pdf_url"): return (p["paper_id"], False, "PDF URL missing")
        for attempt in range(int(os.getenv("RETRY_COUNT", "3"))):
            try:
                data, status, ctype = get(p["pdf_url"], int(os.getenv("REQUEST_TIMEOUT", "45")))
                if status != 200 or not data.startswith(b"%PDF-"): raise ValueError(f"invalid PDF: HTTP {status}, {ctype}")
                path.write_bytes(data); return (p["paper_id"], True, "downloaded")
            except Exception as e:
                if attempt + 1 == int(os.getenv("RETRY_COUNT", "3")): return (p["paper_id"], False, str(e))
                time.sleep(2 ** attempt)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(2, int(os.getenv("MAX_WORKERS", "2"))))) as pool:
        for result in as_completed([pool.submit(one, p) for p in selected]): results.append(result.result())
    for pid, ok, reason in results:
        if not ok: errors.append({"paper_id": pid, "reason": reason})
    write_json(LOGS / "pdf_errors.json", errors)
    return results

def extract_text(papers, limit=None):
    try: import fitz
    except ImportError as e: raise SystemExit("缺少 PyMuPDF：请安装 requirements.txt") from e
    outdir = CACHE / "text"; outdir.mkdir(parents=True, exist_ok=True); selected = papers[:limit] if limit else papers
    for p in selected:
        pdf = CACHE / "pdfs" / f'{p["paper_id"]}.pdf'; txt = outdir / f'{p["paper_id"]}.txt'
        if txt.exists() and txt.stat().st_size > 100: continue
        if not pdf.exists(): continue
        try:
            with fitz.open(pdf) as doc: text = "\n".join(page.get_text() for page in doc)
            if text.strip(): txt.write_text(text, encoding="utf-8")
        except Exception as e: write_json(LOGS / f'extract_{p["paper_id"]}.json', {"reason": str(e)})

def analysis_input(paper):
    text_path = CACHE / "text" / f'{paper["paper_id"]}.txt'
    full_text = text_path.read_text(encoding="utf-8").strip() if text_path.exists() else ""
    metadata = {key: paper[key] for key in ("conference", "year", "authors", "original_session", "normalized_session", "doi", "pages", "isca_page_url") if paper.get(key)}
    return {"source": "full_text" if full_text else "abstract", "full_text": full_text, "abstract": paper.get("abstract", ""), "title": paper.get("title", ""), "official_metadata": metadata}

def analysis_schema(obj):
    try:
        import jsonschema
        jsonschema.validate(obj, PAPER_SCHEMA)
        return True
    except Exception:
        return False

def analyze(limit=None):
    key = os.getenv("OPENAI_API_KEY"); base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"); model = os.getenv("OPENAI_MODEL")
    if not key or not model: raise SystemExit("ANALYSIS PIPELINE BLOCKED: MODEL CREDENTIAL REQUIRED\n请配置 OPENAI_API_KEY、OPENAI_BASE_URL（OpenAI-compatible API 地址）和 OPENAI_MODEL。")
    papers = read_json(DATA / "papers.json", []); analyses = read_json(DATA / "paper_analysis.json", {})
    items = []
    for p in papers:
        inp = analysis_input(p)
        digest = hashlib.sha256(json.dumps(inp, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if analyses.get(p["paper_id"], {}).get("source_hash") != digest and (not limit or len(items) < limit): items.append(p)
    errors = []
    for p in items:
        txt = (CACHE / "text" / f'{p["paper_id"]}.txt')
        inp = analysis_input(p)
        source_kind = inp["source"]
        digest = hashlib.sha256(json.dumps(inp, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        prompt = {"paper_id": p["paper_id"], **inp}
        system = "你是精读论文的研究者。参考ICLR2026-Guide-CN的六维摘要方式，用中文简洁说明研究动机、解决问题、现象分析、主要方法、数据与实验、主要贡献。只依据所给论文标题、ISCA官方元数据、官方摘要和（若提供）正文，不根据领域常识补充。正文和摘要冲突时优先正文。数值、Dataset、Metric、语言、模型、实验设置和结果必须有明确文本证据；没有写明就留空/写明论文未报告，不做推测。若没有明确现象发现，现象分析写“论文未单独报告明确的现象性发现，主要结果集中于……”并仅补正文明确支持的结果。structured必须按schema填写topics、tasks、method_families（方法大类）、specific_methods（论文明确使用/提出的具体技术）、models、representations、datasets、metrics、languages、modalities、application_domains、contribution_types；没有可靠证据填空数组。对重要数字、dataset、metric、experiment setup、key finding及方法事实，写入evidence数组：每项包含claim_type、对应field、原文逐字短摘录evidence_text、能可靠定位时的页码或section（不能可靠定位则空字符串）和source（full_text或abstract）；不得把转述伪装成引文。dataset_entities的new_or_existing仅用new/expanded/reannotated/curated/existing/unknown；availability仅用public/restricted/not_stated/unknown；evaluation_entities的type仅用benchmark/metric/protocol/human/robustness/reliability/diagnostic/other，automatic_or_human仅用automatic/human/both/unknown。实体evidence使用原文短摘录。数据集专项只在数据集建设、发布、扩充、标注、治理或系统研究本身是重要贡献时相关；实验使用数据集不算。评测专项只在评测、benchmark、metric、protocol、诊断、可靠性、鲁棒性或人工评测本身是重要贡献时相关。只输出符合 JSON Schema 的 JSON，不要Markdown。"
        payload = {"model": model, "temperature": 0, "response_format": {"type":"json_object"}, "messages":[{"role":"system","content":system},{"role":"user","content":json.dumps(prompt,ensure_ascii=False)}]}
        try:
            obj = None
            for attempt in range(int(os.getenv("RETRY_COUNT", "3"))):
                try:
                    req = Request(base + "/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
                    raw, _, _ = get_json_request(req)
                    obj = json.loads(raw["choices"][0]["message"]["content"])
                    if obj.get("paper_id") != p["paper_id"]: raise ValueError("paper_id mismatch")
                    if not analysis_schema(obj): raise ValueError("JSON schema validation failed")
                    break
                except Exception:
                    if attempt + 1 == int(os.getenv("RETRY_COUNT", "3")): raise
                    time.sleep(2 ** attempt)
            obj.update(paper_id=p["paper_id"], analysis_source=source_kind, source_isca_page=p["isca_page_url"], source_pdf=p.get("pdf_url", ""), source_hash=digest)
            analyses[p["paper_id"]] = obj
            write_json(DATA / "paper_analysis.json", analyses)
        except Exception as e: errors.append({"paper_id":p["paper_id"],"title":p["title"],"reason":str(e)})
    write_json(LOGS / "analysis_errors.json", errors)

def get_json_request(req):
    with urlopen(req, timeout=int(os.getenv("REQUEST_TIMEOUT", "45"))) as r: return json.loads(r.read()), r.status, r.headers.get("Content-Type", "")

def aggregate(papers):
    analyses = read_json(DATA / "paper_analysis.json", {}); all_papers = []
    dataset_aliases = read_json(DATA / "dataset_aliases.json", {})
    metric_aliases = read_json(DATA / "metric_aliases.json", {})
    for p in papers:
        row = dict(p); a = analyses.get(p["paper_id"], {})
        row["analysis"] = a.get("analysis", {}); row["structured"] = a.get("structured", {}); row["dataset_entities"] = a.get("dataset_entities", []); row["evaluation_entities"] = a.get("evaluation_entities", []); row["special_tags"] = a.get("special_tags", {"dataset":{"relevant":None,"confidence":None,"reason":""},"evaluation":{"relevant":None,"confidence":None,"reason":""}}); row["analysis_source"] = a.get("analysis_source", "pending"); row["evidence"] = a.get("evidence", [])
        row["structured"]["datasets"] = [dataset_aliases.get(name, name) for name in row["structured"].get("datasets", [])]
        row["structured"]["metrics"] = [metric_aliases.get(name, name) for name in row["structured"].get("metrics", [])]
        all_papers.append(row)
    manifest = read_json(RAW / "crawl_manifest.json", {})
    denom = max(1, manifest.get("listed_count", len(all_papers)))
    def stats(rows):
        def top(*fields):
            counts = {}
            for p in rows:
                for field in fields:
                    for value in set(p.get("structured", {}).get(field, [])):
                        if isinstance(value, str) and value.strip(): counts[value] = counts.get(value, 0) + 1
            return [{"name":k,"count":v} for k,v in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:10]]
        return {"paper_count":len(rows),"percentage":round(100*len(rows)/denom,2),"top_topics":top("topics"),"top_tasks":top("tasks"),"top_methods":top("method_families","specific_methods"),"top_datasets":top("datasets"),"top_metrics":top("metrics"),"top_languages":top("languages"),"top_modalities":top("modalities"),"top_domains":top("application_domains"),"contribution_types":top("contribution_types")}
    sessions = sorted({p["normalized_session"] for p in all_papers})
    dataset_papers = [p for p in all_papers if p["special_tags"].get("dataset",{}).get("relevant") and p["special_tags"]["dataset"].get("confidence") in ("high","medium")]
    evaluation_papers = [p for p in all_papers if p["special_tags"].get("evaluation",{}).get("relevant") and p["special_tags"]["evaluation"].get("confidence") in ("high","medium")]
    def entity_counts(rows, key, field):
        counts = {}
        for paper in rows:
            for entity in paper.get(key, []):
                value = entity.get(field, "")
                if isinstance(value, list):
                    for item in set(value): counts[item] = counts.get(item, 0) + 1
                elif value: counts[value] = counts.get(value, 0) + 1
        return [{"name":k,"count":v} for k,v in sorted(counts.items(),key=lambda x:(-x[1],x[0]))[:10]]
    dataset_detail = {"new_dataset_count":sum(any(e.get("new_or_existing")=="new" for e in p["dataset_entities"]) for p in dataset_papers),"expanded_or_curated_count":sum(any(e.get("new_or_existing") in ("expanded","reannotated","curated") for e in p["dataset_entities"]) for p in dataset_papers),"benchmark_dataset_count":sum(any(e.get("role")=="benchmark" for e in p["dataset_entities"]) for p in dataset_papers),"public_dataset_count":sum(any(e.get("availability")=="public" for e in p["dataset_entities"]) for p in dataset_papers),"top_entities":entity_counts(dataset_papers,"dataset_entities","name"),"collection_methods":entity_counts(dataset_papers,"dataset_entities","collection_method"),"annotation_methods":entity_counts(dataset_papers,"dataset_entities","annotation_method"),"reported_scales":[{"paper_id":p["paper_id"],"title":p["title"],"dataset":e["name"],"scale_text":e["scale_text"]} for p in dataset_papers for e in p["dataset_entities"] if e.get("scale_text")]}
    evaluation_detail = {"benchmark_paper_count":sum(any(e.get("type")=="benchmark" for e in p["evaluation_entities"]) for p in evaluation_papers),"metric_paper_count":sum(any(e.get("type")=="metric" for e in p["evaluation_entities"]) for p in evaluation_papers),"protocol_paper_count":sum(any(e.get("type")=="protocol" for e in p["evaluation_entities"]) for p in evaluation_papers),"human_evaluation_paper_count":sum(any(e.get("automatic_or_human") in ("human","both") for e in p["evaluation_entities"]) for p in evaluation_papers),"robustness_reliability_paper_count":sum(any(e.get("type") in ("robustness","reliability") for e in p["evaluation_entities"]) for p in evaluation_papers),"top_evaluation_entities":entity_counts(evaluation_papers,"evaluation_entities","name"),"evaluation_modes":entity_counts(evaluation_papers,"evaluation_entities","automatic_or_human")}
    analysis_count = sum(all(p.get("analysis", {}).get(field) for field in FIELDS) for p in all_papers)
    model_ready = bool(os.getenv("OPENAI_API_KEY") and os.getenv("OPENAI_MODEL"))
    status = "complete" if analysis_count == len(all_papers) else "partial" if model_ready else "blocked"
    taxonomy = read_json(DATA / "taxonomy.json", {})
    session_to_macro = taxonomy.get("session_to_macro", {})
    out = {"source_listed_count":manifest.get("listed_count",len(all_papers)),"data_scope":"sample" if manifest.get("sample_limit") else "full","analysis_complete_count":analysis_count,"analysis_total_count":len(all_papers),"analysis_status":status,"all":stats(all_papers),"categories":{s:stats([p for p in all_papers if p["normalized_session"]==s]) for s in sessions},"macro_categories":{macro["id"]:stats([p for p in all_papers if session_to_macro.get(p["normalized_session"])==macro["id"]]) for macro in taxonomy.get("macros", [])},"special":{"dataset":{**stats(dataset_papers),**dataset_detail},"evaluation":{**stats(evaluation_papers),**evaluation_detail}}}
    if analysis_count < len(all_papers):
        for values in out["special"].values():
            for key, value in values.items():
                if isinstance(value, (int, float)):
                    values[key] = None
    write_json(DATA / "papers.json", all_papers); write_json(DATA / "stats.json", out)
    return out

def generate_narratives():
    key = os.getenv("OPENAI_API_KEY"); base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"); model = os.getenv("OPENAI_MODEL")
    if not key or not model: raise SystemExit("ANALYSIS PIPELINE BLOCKED: MODEL CREDENTIAL REQUIRED\n请配置 OPENAI_API_KEY、OPENAI_BASE_URL（OpenAI-compatible API 地址）和 OPENAI_MODEL。")
    stats = read_json(DATA / "stats.json", {})
    if not stats: raise SystemExit("请先运行 stats 生成程序统计。")
    path = DATA / "category_analysis.json"; cache = read_json(path, {}); errors = []
    entries = {"all": stats["all"], **{"category:" + k: v for k, v in stats["categories"].items()}, "special:dataset": stats["special"]["dataset"], "special:evaluation": stats["special"]["evaluation"]}
    schema = {"type":"object","required":["overview","research_hotspots","major_results","trend_summary"],"additionalProperties":False,"properties":{"overview":{"type":"string"},"research_hotspots":{"type":"array","items":{"type":"string"}},"major_results":{"type":"array","items":{"type":"string"}},"trend_summary":{"type":"string"}}}
    for name, values in entries.items():
        digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if cache.get(name, {}).get("source_hash") == digest: continue
        prompt = "根据下面由程序计算的真实统计，生成中文分析。不得添加输入之外的数量、比例、排名或论文事实；不要从0个实体推断趋势。字段 overview、research_hotspots、major_results、trend_summary，严格输出JSON。\n" + json.dumps({"scope":name,"statistics":values},ensure_ascii=False)
        payload = {"model":model,"temperature":0,"response_format":{"type":"json_object"},"messages":[{"role":"system","content":"严谨的学术会议综述分析者，只对已计算统计做描述，不补造事实。"},{"role":"user","content":prompt}]}
        try:
            result = None
            for attempt in range(int(os.getenv("RETRY_COUNT", "3"))):
                try:
                    req = Request(base + "/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
                    raw, _, _ = get_json_request(req); result = json.loads(raw["choices"][0]["message"]["content"])
                    import jsonschema; jsonschema.validate(result, schema); break
                except Exception:
                    if attempt + 1 == int(os.getenv("RETRY_COUNT", "3")): raise
                    time.sleep(2 ** attempt)
            cache[name] = {**result,"source_hash":digest}; write_json(path,cache)
        except Exception as e: errors.append({"scope":name,"reason":str(e)})
    write_json(LOGS / "category_analysis_errors.json", errors)

def validate(papers, stats):
    manifest=read_json(RAW/"crawl_manifest.json",{}); ids=[p.get("paper_id") for p in papers]
    analyses=read_json(DATA/"paper_analysis.json",{})
    complete = sum(all(p.get("analysis",{}).get(f) for f in FIELDS) for p in papers)
    pdf_ok = sum((CACHE/"pdfs"/(p["paper_id"]+".pdf")).exists() for p in papers)
    text_ok = sum((CACHE/"text"/(p["paper_id"]+".txt")).exists() for p in papers)
    doi_values=[p.get("doi", "").lower() for p in papers if p.get("doi")]
    titles=[p.get("title", "").casefold() for p in papers if p.get("title")]
    taxonomy = read_json(DATA / "taxonomy.json", {})
    normalized_sessions = set(manifest.get("normalized_sessions", []))
    raw_sessions = set(manifest.get("listed_sessions", []))
    taxonomy_sessions = taxonomy.get("session_to_macro", {})
    valid_macro_ids = {m.get("id") for m in taxonomy.get("macros", [])}
    expected_raw_map = {name: read_json(DATA / "category_aliases.json", {}).get(name, name) for name in raw_sessions}
    taxonomy_raw_map = taxonomy.get("raw_session_to_normalized_session", {})
    taxonomy_valid = set(taxonomy_sessions) == normalized_sessions and len(taxonomy_sessions) == len(normalized_sessions) and all(macro_id in valid_macro_ids for macro_id in taxonomy_sessions.values()) and set(taxonomy.get("raw_sessions", [])) == raw_sessions and taxonomy_raw_map == expected_raw_map
    def evidence_valid(paper):
        entries = analyses.get(paper["paper_id"], {}).get("evidence", [])
        if not entries: return False
        def evidence_key(value):
            normalized = unicodedata.normalize("NFKC", value).casefold()
            return "".join(ch for ch in normalized if not ch.isspace() and ch not in "-‐‑‒–—")
        abstract = evidence_key(paper.get("abstract", ""))
        text_path = CACHE / "text" / f'{paper["paper_id"]}.txt'
        full_text = evidence_key(text_path.read_text(encoding="utf-8")) if text_path.exists() else ""
        for item in entries:
            source_text = full_text if item.get("source") == "full_text" else abstract
            quote = evidence_key(item.get("evidence_text", ""))
            if not quote or quote not in source_text: return False
        return True
    evidence_ok = sum(evidence_valid(p) for p in papers)
    schema_ok = sum(analysis_schema(analyses.get(p["paper_id"], {})) for p in papers)
    report={"official_listed_count":manifest.get("listed_count",0),"sample_size":len(papers),"parsed_papers":len(papers),"metadata_failures":manifest.get("errors",0),"missing_from_details":manifest.get("listed_count",0)-len(papers),"unique_paper_ids":len(set(ids)),"duplicate_paper_ids":len(ids)-len(set(ids)),"missing_titles":sum(not p.get("title") for p in papers),"missing_authors":sum(not p.get("authors") for p in papers),"missing_abstracts":sum(not p.get("abstract") for p in papers),"missing_isca_urls":sum(not p.get("isca_page_url") for p in papers),"duplicate_dois":len(doi_values)-len(set(doi_values)),"duplicate_titles":len(titles)-len(set(titles)),"raw_session_count":len({p.get("original_session") for p in papers}),"official_raw_session_count":len(manifest.get("listed_sessions", [])),"normalized_session_count":len({p.get("normalized_session") for p in papers}),"official_normalized_session_count":len(manifest.get("normalized_sessions", [])),"session_alias_count":manifest.get("session_alias_count",0),"session_normalization_pass":manifest.get("session_normalization_pass",False) and all(p.get("normalized_session") for p in papers),"taxonomy_macro_count":len(taxonomy.get("macros", [])),"taxonomy_session_count":len(taxonomy_sessions),"taxonomy_coverage_pass":taxonomy_valid,"analysis_schema_valid_count":schema_ok,"six_dimension_complete":complete,"analysis_source_full_text":sum(p.get("analysis_source")=="full_text" for p in papers),"analysis_source_abstract":sum(p.get("analysis_source")=="abstract" for p in papers),"structured_extraction_complete":sum(all(isinstance(analyses.get(p["paper_id"],{}).get("structured",{}).get(k),list) for k in ARRAY_FIELDS) for p in papers),"special_judgment_complete":sum(all(isinstance(analyses.get(p["paper_id"],{}).get("special_tags",{}).get(k,{}).get("relevant"),bool) and analyses.get(p["paper_id"],{}).get("special_tags",{}).get(k,{}).get("confidence") in ("high","medium","low") for k in ("dataset","evaluation")) for p in papers),"evidence_trace_complete":evidence_ok,"evidence_items_total":sum(len(analyses.get(p["paper_id"],{}).get("evidence",[])) for p in papers),"dataset_special_count":stats["special"]["dataset"]["paper_count"],"evaluation_special_count":stats["special"]["evaluation"]["paper_count"],"stats_count_matches":stats["all"]["paper_count"]==len(papers),"pdf_success_count":pdf_ok,"pdf_failure_count":len(papers)-pdf_ok,"full_text_success_count":text_ok,"full_text_failure_count":len(papers)-text_ok,"analysis_block_reason":"MODEL CREDENTIAL REQUIRED" if stats.get("analysis_status")=="blocked" else ""}
    report["metadata_sample_pass"] = report["sample_size"]==15 and not any(report[k] for k in ("missing_titles","missing_authors","missing_abstracts","missing_isca_urls","duplicate_paper_ids"))
    report["pdf_sample_pass"] = pdf_ok==15
    report["full_text_sample_pass"] = text_ok==15
    report["analysis_sample_pass"] = complete==15
    report["structured_sample_pass"] = report["structured_extraction_complete"]==15
    report["special_judgment_sample_pass"] = report["special_judgment_complete"]==15
    report["category_count_sum"] = sum(x["paper_count"] for x in stats["categories"].values())
    report["category_count_sum_matches"] = report["category_count_sum"]==len(papers)
    report["stats_sample_pass"] = report["stats_count_matches"] and report["category_count_sum_matches"] and stats["all"]["paper_count"]==15 and report["structured_extraction_complete"]==15
    report["session_sample_pass"] = report["session_normalization_pass"]
    report["sample_pass"] = all(report[k] for k in ("metadata_sample_pass","pdf_sample_pass","full_text_sample_pass","analysis_sample_pass","structured_sample_pass","special_judgment_sample_pass","stats_sample_pass","session_sample_pass")) and report["taxonomy_coverage_pass"] and report["analysis_schema_valid_count"] == len(papers) and report["evidence_trace_complete"] == len(papers)
    report["overall_pass"] = report["sample_pass"] if manifest.get("sample_limit") else report["sample_pass"] and report["missing_from_details"]==0
    report["incomplete_analyses"] = [{"paper_id":p["paper_id"],"title":p["title"],"reason":"model credentials unavailable"} for p in papers if not all(p.get("analysis",{}).get(f) for f in FIELDS)]
    write_json(DATA/"validation_report.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report

def build_site(papers, stats):
    # Runtime site reads the same structured data that the pipeline validates.
    site=ROOT/"site"; (site/"assets").mkdir(parents=True,exist_ok=True); (site/"data").mkdir(parents=True,exist_ok=True)
    write_json(site/"data"/"papers.json",papers); write_json(site/"data"/"stats.json",stats)
    write_json(site/"data"/"category_analysis.json", read_json(DATA / "category_analysis.json", {}))
    write_json(site/"data"/"taxonomy.json", read_json(DATA / "taxonomy.json", {}))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("command",choices=["crawl","normalize","pdf","extract","analyze","narratives","stats","build","validate","sample"]); ap.add_argument("--limit",type=int)
    args=ap.parse_args(); papers=read_json(DATA/"papers.json",[])
    if args.command=="crawl": papers=crawl(args.limit); papers=normalize_sessions(papers)
    if args.command=="sample": papers=crawl(args.limit or 15); papers=normalize_sessions(papers)
    if args.command in ("pdf","sample"): fetch_pdfs(papers,args.limit or (15 if args.command=="sample" else None))
    if args.command in ("extract","sample"): extract_text(papers,args.limit or (15 if args.command=="sample" else None))
    if args.command=="normalize": papers=normalize_sessions(papers)
    if args.command=="analyze": analyze(args.limit)
    if args.command=="narratives": generate_narratives()
    if args.command in ("stats","build","validate"):
        stats=aggregate(papers)
        if args.command=="build": build_site(read_json(DATA/"papers.json",papers),stats)
        if args.command=="validate": validate(read_json(DATA/"papers.json",papers),stats)
    if args.command=="sample":
        sample_cached = sum((CACHE / "pdfs" / (p["paper_id"] + ".pdf")).exists() for p in papers[:args.limit or 15])
        print(f"Sample metadata={len(papers)}; PDFs cached={sample_cached}")

if __name__=="__main__": main()
