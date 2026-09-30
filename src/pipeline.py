#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISCA Interspeech 2026 官方元数据采集、分析与静态站点构建。"""
import argparse
import copy
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
ARRAY_FIELDS = ["topics", "tasks", "method_families", "specific_methods", "models", "representations", "datasets", "metrics", "languages", "language_details", "modalities", "application_domains", "contribution_types"]
ENTITY_FIELDS = {
    "dataset_entities": ["name", "role", "dataset_role", "new_or_existing", "language", "language_status", "is_multilingual", "is_low_resource", "is_endangered_language", "domain", "modality", "scale_text", "availability", "collection_method", "annotation_method", "evidence"],
    "evaluation_entities": ["name", "type", "task", "automatic_or_human", "objective_or_subjective", "attributes", "is_benchmark", "is_metric_study", "is_protocol_study", "is_robustness", "is_reliability", "is_generalization", "is_fairness", "is_safety", "is_foundation_model_eval", "is_speech_llm_eval", "evidence"],
}
EVIDENCE_TYPES = ["numerical_result", "dataset", "metric", "experiment_setup", "key_finding", "method", "model", "language"]
EVALUATION_ATTRIBUTES = ["objective", "subjective", "foundation_model", "speech_llm", "fairness", "safety", "generalization", "robustness", "reliability"]
ENTITY_ARRAY_FIELDS = {"dataset_entities": {"language", "language_status", "modality"}, "evaluation_entities": {"attributes"}}
ENTITY_BOOL_FIELDS = {"is_multilingual", "is_low_resource", "is_endangered_language", "is_benchmark", "is_metric_study", "is_protocol_study", "is_robustness", "is_reliability", "is_generalization", "is_fairness", "is_safety", "is_foundation_model_eval", "is_speech_llm_eval"}
ENTITY_ENUMS = {
    ("dataset_entities", "role"): ["proposed", "used", "benchmark", "expanded", "reannotated", "curated", "systematic_study", "unknown"],
    ("dataset_entities", "dataset_role"): ["proposed", "used", "benchmark", "expanded", "reannotated", "curated", "systematic_study", "unknown"],
    ("dataset_entities", "new_or_existing"): ["new", "expanded", "reannotated", "curated", "existing", "unknown"],
    ("dataset_entities", "availability"): ["public", "restricted", "not_stated", "unknown"],
    ("dataset_entities", "language_status"): ["low_resource", "endangered"],
    ("evaluation_entities", "type"): ["benchmark", "metric", "protocol", "human", "robustness", "reliability", "diagnostic", "other"],
    ("evaluation_entities", "automatic_or_human"): ["automatic", "human", "both", "unknown"],
    ("evaluation_entities", "objective_or_subjective"): ["objective", "subjective", "both", "unknown"],
    ("evaluation_entities", "attributes"): EVALUATION_ATTRIBUTES,
}

def entity_schema(entity, field):
    if field in ENTITY_ARRAY_FIELDS[entity]:
        item = {"enum": ENTITY_ENUMS[(entity, field)]} if (entity, field) in ENTITY_ENUMS else {"type": "string"}
        return {"type": "array", "items": item}
    if field in ENTITY_BOOL_FIELDS:
        return {"type": "boolean"}
    if (entity, field) in ENTITY_ENUMS:
        return {"type": "string", "enum": ENTITY_ENUMS[(entity, field)]}
    return {"type": "string"}

ENTITY_SCHEMAS = {key: {"type": "array", "items": {"type": "object", "required": fields, "additionalProperties": False, "properties": {field: entity_schema(key, field) for field in fields}}} for key, fields in ENTITY_FIELDS.items()}
PAPER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": ["paper_id", "tldr_cn", "analysis", "structured", *ENTITY_FIELDS, "special_tags", "evidence"], "additionalProperties": False,
    "properties": {
        "paper_id": {"type": "string", "minLength": 1},
        "tldr_cn": {"type": "string", "minLength": 1},
        "analysis_source": {"enum": ["full_text", "abstract"]},
        "source_isca_page": {"type": "string"},
        "source_pdf": {"type": "string"},
        "source_hash": {"type": "string"},
        "analysis": {"type": "object", "required": FIELDS, "additionalProperties": False, "properties": {k: {"type": "string", "minLength": 1} for k in FIELDS}},
        "structured": {"type": "object", "required": ARRAY_FIELDS, "additionalProperties": False, "properties": {k: {"type": "array", "items": {"type": "string"}} for k in ARRAY_FIELDS}},
        **ENTITY_SCHEMAS,
        "special_tags": {"type": "object", "required": ["dataset", "evaluation"], "additionalProperties": False, "properties": {k: {"type": "object", "required": ["relevant", "reason", "confidence"], "additionalProperties": False, "properties": {"relevant": {"type": "boolean"}, "reason": {"type": "string"}, "confidence": {"enum": ["high", "medium", "low"]}}} for k in ("dataset", "evaluation")}},
        "evidence": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["claim_type", "field", "evidence_text", "page_or_section", "source"], "additionalProperties": False, "properties": {"claim_type": {"enum": EVIDENCE_TYPES}, "field": {"type": "string", "minLength": 1}, "evidence_text": {"type": "string", "minLength": 1}, "page_or_section": {"type": "string"}, "source": {"enum": ["full_text", "abstract"]}}}},
    },
}

def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def read_analyses():
    analyses = read_json(DATA / "paper_analysis.json", {})
    evidence = read_json(DATA / "internal_evidence.json", {})
    for paper_id, record in analyses.items():
        private = evidence.get(paper_id, {})
        if private.get("evidence"):
            record["evidence"] = private["evidence"]
        for field in ("dataset_entities", "evaluation_entities"):
            quotes = private.get(field, [])
            if quotes:
                for index, entity in enumerate(record.get(field, [])):
                    if index < len(quotes): entity["evidence"] = quotes[index]
    return analyses

def write_analyses(analyses):
    private = read_json(DATA / "internal_evidence.json", {})
    clean = copy.deepcopy(analyses)
    for paper_id, record in clean.items():
        saved = {"evidence": record.pop("evidence", [])}
        for field in ("dataset_entities", "evaluation_entities"):
            saved[field] = [entity.pop("evidence", "") for entity in record.get(field, [])]
        if any(saved["evidence"]) or any(any(saved[field]) for field in ("dataset_entities", "evaluation_entities")):
            private[paper_id] = saved
        else:
            private.pop(paper_id, None)
    write_json(DATA / "internal_evidence.json", private)
    write_json(DATA / "paper_analysis.json", clean)

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

def crawl(limit=None, output_path=None, manifest_path=None, errors_path=None):
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
    for done, f in enumerate(as_completed(futures), 1):
        row = f.result()
        if "error" in row: errors.append({"paper_id": row["paper"]["paper_id"], "title": row["paper"]["title"], "reason": row["error"]})
        else: papers.append(row)
        if done % 50 == 0 or done == len(futures): print(f"Official detail pages: {done}/{len(futures)}", flush=True)
    papers.sort(key=lambda p: p["paper_id"])
    write_json(output_path or DATA / "papers.json", papers); write_json(errors_path or LOGS / "crawl_errors.json", errors)
    # Keep the raw count even if a detail page fails so validation can detect loss.
    write_json(manifest_path or RAW / "crawl_manifest.json", {"proceedings_url": PROCEEDINGS, "listed_count": listed_count, "listed_sessions": listed_sessions, "sample_limit": limit, "parsed_detail_count": len(papers), "errors": len(errors)})
    return papers

def normalize_sessions(papers, manifest_path=None, output_path=None):
    aliases_path = DATA / "category_aliases.json"; aliases = read_json(aliases_path, {})
    session_labels = read_json(DATA / "session_labels_zh.json", {})
    manifest_path = manifest_path or RAW / "crawl_manifest.json"
    groups = {}
    session_names = read_json(manifest_path, {}).get("listed_sessions", [p["original_session"] for p in papers])
    for session in session_names:
        m = re.match(r"^(.*?)(?:\s+(?:Part\s+)?(\d+|I|II|III|IV|V))$", session, re.I)
        if m: groups.setdefault(m.group(1).strip(), set()).add(m.group(2).upper())
    for base, numbers in groups.items():
        if len(numbers) > 1:
            for session in session_names:
                if re.match(rf"^{re.escape(base)}\s+(?:Part\s+)?(?:\d+|I|II|III|IV|V)$", session, re.I): aliases.setdefault(session, base)
    manifest = read_json(manifest_path, {})
    manifest["normalized_sessions"] = sorted({aliases.get(session, session) for session in session_names})
    manifest["session_alias_count"] = len(aliases)
    manifest["session_normalization_pass"] = all(aliases.get(session) == base for base, numbers in groups.items() if len(numbers) > 1 for session in session_names if re.match(rf"^{re.escape(base)}\s+(?:Part\s+)?(?:\d+|I|II|III|IV|V)$", session, re.I))
    write_json(manifest_path, manifest)
    write_json(aliases_path, aliases)
    taxonomy = read_json(DATA / "taxonomy.json", {})
    macro_names = {macro["id"]: macro["name"] for macro in taxonomy.get("macros", [])}
    session_to_macro = taxonomy.get("session_to_macro", {})
    for p in papers:
        p["normalized_session"] = aliases.get(p["original_session"], p["original_session"])
        p["session_zh"] = session_labels.get(p["normalized_session"], "")
        p["macro_id"] = session_to_macro.get(p["normalized_session"], "")
        p["macro_zh"] = macro_names.get(p["macro_id"], "")
    write_json(output_path or DATA / "papers.json", papers)
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

def analyze(limit=None, papers_path=None):
    key = os.getenv("OPENAI_API_KEY"); base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"); model = os.getenv("OPENAI_MODEL")
    if not key or not model: raise SystemExit("FULL ANALYSIS BLOCKED: MODEL ACCESS REQUIRED\n请配置 OPENAI_API_KEY、OPENAI_MODEL；兼容服务另配置 OPENAI_BASE_URL。")
    papers = read_json(papers_path or DATA / "papers.json", []); analyses = read_analyses()
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
        system = "你是精读论文的研究者。先输出一句约30–70个中文字的 tldr_cn，概括论文做什么及其最重要结果或意义；再参考 ICLR2026-Guide-CN 六维格式简洁说明研究动机、解决问题、现象分析、主要方法、数据与实验、主要贡献。只依据标题、官方元数据、摘要和（若提供）正文，不根据领域常识补充。正文和摘要冲突时优先正文。数字、Dataset、Metric、语言、模型、实验设置和结果必须有明确文本证据；没有证据就留空或写明未报告。structured按schema填topics、tasks、method_families（方法大类）、specific_methods（具体技术）、models、representations、datasets、metrics、languages、language_details（地区/方言等原始细分）、modalities、application_domains、contribution_types；无可靠信息填空数组。dataset_entities.language_status只填写论文明确称为low_resource或endangered的语言标签；不要按语言常识猜测。evaluation_entities.attributes可用objective、subjective、foundation_model、speech_llm、fairness、safety、generalization、robustness、reliability，只填正文明确支持的属性。对重要数字、dataset、metric、experiment setup、key finding及方法事实，在evidence中保存claim_type、field、原文逐字短摘录evidence_text、可靠的页码或section和source（full_text或abstract）；不得把转述伪装成引文。dataset_entities的new_or_existing仅用new/expanded/reannotated/curated/existing/unknown；availability仅用public/restricted/not_stated/unknown；evaluation_entities的type仅用benchmark/metric/protocol/human/robustness/reliability/diagnostic/other，automatic_or_human仅用automatic/human/both/unknown。实体evidence使用原文短摘录。数据集专项只在数据集建设、发布、扩充、标注、治理或系统研究本身是重要贡献时相关；实验使用数据集不算。评测专项只在评测、benchmark、metric、protocol、诊断、可靠性、鲁棒性或人工评测本身是重要贡献时相关。只输出符合 JSON Schema 的 JSON，不要Markdown。"
        system += " Dataset实体字段必须包括dataset_role、is_multilingual、is_low_resource、is_endangered_language；评测实体字段必须包括objective_or_subjective，以及is_benchmark、is_metric_study、is_protocol_study、is_robustness、is_reliability、is_generalization、is_fairness、is_safety、is_foundation_model_eval、is_speech_llm_eval。只依据论文明确证据填写；不能确认的布尔值填false，枚举值填unknown。"
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
            write_analyses(analyses)
        except Exception as e: errors.append({"paper_id":p["paper_id"],"title":p["title"],"reason":str(e)})
    write_json(LOGS / "analysis_errors.json", errors)

def get_json_request(req):
    with urlopen(req, timeout=int(os.getenv("REQUEST_TIMEOUT", "45"))) as r: return json.loads(r.read()), r.status, r.headers.get("Content-Type", "")

def aggregate(papers):
    analyses = read_analyses()
    session_labels = read_json(DATA / "session_labels_zh.json", {})
    aliases = {key: read_json(DATA / f"{key}_aliases.json", {}) for key in ("dataset", "metric", "language", "modality", "method_family")}
    rows = []
    for paper in papers:
        analysis = analyses.get(paper["paper_id"], {})
        metadata = {key: value for key, value in paper.items() if key not in {"tldr_cn", "analysis", "structured", "dataset_entities", "evaluation_entities", "special_tags", "analysis_source", "evidence"}}
        metadata["session_zh"] = session_labels.get(paper.get("normalized_session", ""), "")
        structured = copy.deepcopy(analysis.get("structured", {}))
        for field, alias_key in (("datasets", "dataset"), ("metrics", "metric"), ("languages", "language"), ("modalities", "modality"), ("method_families", "method_family")):
            alias = aliases[alias_key]
            structured[field] = list(dict.fromkeys(alias.get(value, value) for value in structured.get(field, [])))
        dataset_entities = copy.deepcopy(analysis.get("dataset_entities", []))
        for entity in dataset_entities:
            for field, alias_key in (("name", "dataset"), ("language", "language"), ("modality", "modality")):
                values = [entity.get(field, "")] if field == "name" else entity.get(field, [])
                normalized = list(dict.fromkeys(aliases[alias_key].get(value, value) for value in values if value))
                entity[field] = normalized[0] if field == "name" and normalized else ("" if field == "name" else normalized)
        rows.append({
            **metadata,
            "tldr_cn": analysis.get("tldr_cn", ""),
            "analysis": analysis.get("analysis", {}),
            "structured": structured,
            "dataset_entities": dataset_entities,
            "evaluation_entities": copy.deepcopy(analysis.get("evaluation_entities", [])),
            "special_tags": copy.deepcopy(analysis.get("special_tags", {})),
            "analysis_source": analysis.get("analysis_source", "pending"),
            "evidence": copy.deepcopy(analysis.get("evidence", [])),
        })

    manifest = read_json(RAW / "crawl_manifest.json", {})
    official_count = max(1, manifest.get("listed_count", len(rows)))
    complete = lambda p: bool(p.get("tldr_cn")) and all(p.get("analysis", {}).get(field) for field in FIELDS) and all(isinstance(p.get("structured", {}).get(field), list) for field in ARRAY_FIELDS) and all(isinstance(p.get("special_tags", {}).get(key, {}).get("relevant"), bool) and p.get("special_tags", {}).get(key, {}).get("confidence") in ("high", "medium", "low") for key in ("dataset", "evaluation"))
    analyzed = [p for p in rows if complete(p)]
    analysis_count = len(analyzed)
    data_scope = "full" if manifest.get("sample_limit") is None and len(rows) == official_count and len(analyzed) == official_count else "partial" if len(rows) == official_count else "sample"
    denom = max(1, analysis_count)

    def top(rows_in, *fields):
        counts = {}
        for paper in rows_in:
            for field in fields:
                for value in set(paper.get("structured", {}).get(field, [])):
                    if isinstance(value, str) and value.strip(): counts[value] = counts.get(value, 0) + 1
        return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]]

    def unique_count(rows_in, field):
        return len({value for paper in rows_in for value in paper.get("structured", {}).get(field, []) if isinstance(value, str) and value.strip()})

    def stats(rows_in):
        count = len(rows_in)
        result = {
            "paper_count": count,
            "sample_paper_count": count,
            "sample_share_percentage": round(100 * count / denom, 2),
            "coverage_percentage": round(100 * analysis_count / official_count, 2),
            "unique_task_count": unique_count(rows_in, "tasks"),
            "unique_dataset_count": unique_count(rows_in, "datasets"),
            "unique_language_count": unique_count(rows_in, "languages"),
            "top_topics": top(rows_in, "topics"),
            "top_tasks": top(rows_in, "tasks"),
            "top_method_families": top(rows_in, "method_families"),
            "top_specific_methods": top(rows_in, "specific_methods"),
            "top_datasets": top(rows_in, "datasets"),
            "top_metrics": top(rows_in, "metrics"),
            "top_languages": top(rows_in, "languages"),
            "top_modalities": top(rows_in, "modalities"),
            "top_domains": top(rows_in, "application_domains"),
            "contribution_types": top(rows_in, "contribution_types"),
        }
        if data_scope == "full": result["conference_share_percentage"] = round(100 * count / official_count, 2)
        return result

    def entity_counts(rows_in, key, field):
        counts = {}
        for paper in rows_in:
            paper_values = set()
            for entity in paper.get(key, []):
                values = entity.get(field, "")
                values = values if isinstance(values, list) else [values]
                paper_values.update(item for item in values if item)
            for value in paper_values: counts[value] = counts.get(value, 0) + 1
        return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]]

    def papers_with(rows_in, predicate):
        return sum(bool(predicate(paper)) for paper in rows_in)

    dataset_papers = [p for p in analyzed if p.get("special_tags", {}).get("dataset", {}).get("relevant") is True]
    evaluation_papers = [p for p in analyzed if p.get("special_tags", {}).get("evaluation", {}).get("relevant") is True]
    dataset_detail = {
        "new_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("new_or_existing") == "new" for e in p["dataset_entities"])),
        "expanded_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("new_or_existing") == "expanded" for e in p["dataset_entities"])),
        "reannotated_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("new_or_existing") == "reannotated" for e in p["dataset_entities"])),
        "curated_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("new_or_existing") == "curated" for e in p["dataset_entities"])),
        "expanded_or_curated_count": papers_with(dataset_papers, lambda p: any(e.get("new_or_existing") in ("expanded", "reannotated", "curated") for e in p["dataset_entities"])),
        "benchmark_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("dataset_role") == "benchmark" for e in p["dataset_entities"])),
        "public_dataset_count": papers_with(dataset_papers, lambda p: any(e.get("availability") == "public" for e in p["dataset_entities"])),
        "multilingual_paper_count": papers_with(dataset_papers, lambda p: any(e.get("is_multilingual") for e in p["dataset_entities"])),
        "low_resource_paper_count": papers_with(dataset_papers, lambda p: any(e.get("is_low_resource") for e in p["dataset_entities"])),
        "endangered_language_paper_count": papers_with(dataset_papers, lambda p: any(e.get("is_endangered_language") for e in p["dataset_entities"])),
        "top_entities": entity_counts(dataset_papers, "dataset_entities", "name"),
        "language_distribution": entity_counts(dataset_papers, "dataset_entities", "language"),
        "modality_distribution": entity_counts(dataset_papers, "dataset_entities", "modality"),
        "availability_distribution": entity_counts(dataset_papers, "dataset_entities", "availability"),
        "dataset_role_distribution": entity_counts(dataset_papers, "dataset_entities", "dataset_role"),
        "collection_methods": entity_counts(dataset_papers, "dataset_entities", "collection_method"),
        "annotation_methods": entity_counts(dataset_papers, "dataset_entities", "annotation_method"),
        "task_distribution": top(dataset_papers, "tasks"),
        "reported_scales": [{"paper_id": p["paper_id"], "title": p["title"], "dataset": e["name"], "scale_text": e["scale_text"]} for p in dataset_papers for e in p["dataset_entities"] if e.get("scale_text")],
    }
    attr_counts = {attribute: papers_with(evaluation_papers, lambda p, a=attribute: any(a in e.get("attributes", []) for e in p["evaluation_entities"])) for attribute in EVALUATION_ATTRIBUTES}
    mode_counts = {mode: papers_with(evaluation_papers, lambda p, m=mode: any(e.get("automatic_or_human") == m for e in p["evaluation_entities"])) for mode in ("automatic", "human", "both")}
    evaluation_flags = {
        "benchmark_paper_count": "is_benchmark", "metric_paper_count": "is_metric_study", "protocol_paper_count": "is_protocol_study",
        "robustness_evaluation_count": "is_robustness", "reliability_evaluation_count": "is_reliability", "generalization_evaluation_count": "is_generalization",
        "fairness_evaluation_count": "is_fairness", "safety_evaluation_count": "is_safety", "foundation_model_evaluation_count": "is_foundation_model_eval", "speech_llm_evaluation_count": "is_speech_llm_eval",
    }
    evaluation_detail = {
        **{key: papers_with(evaluation_papers, lambda p, flag=flag: any(e.get(flag) for e in p["evaluation_entities"])) for key, flag in evaluation_flags.items()},
        "automatic_evaluation_count": mode_counts["automatic"],
        "human_evaluation_count": mode_counts["human"],
        "both_evaluation_count": mode_counts["both"],
        "evaluation_mode_distribution": entity_counts(evaluation_papers, "evaluation_entities", "automatic_or_human"),
        "objective_subjective_distribution": entity_counts(evaluation_papers, "evaluation_entities", "objective_or_subjective"),
        "attribute_evaluation_counts": attr_counts,
        "objective_evaluation_count": papers_with(evaluation_papers, lambda p: any(e.get("objective_or_subjective") in ("objective", "both") for e in p["evaluation_entities"])),
        "subjective_evaluation_count": papers_with(evaluation_papers, lambda p: any(e.get("objective_or_subjective") in ("subjective", "both") for e in p["evaluation_entities"])),
        "robustness_reliability_paper_count": papers_with(evaluation_papers, lambda p: any(e.get("is_robustness") or e.get("is_reliability") for e in p["evaluation_entities"])),
        "top_evaluation_entities": entity_counts(evaluation_papers, "evaluation_entities", "name"),
        "evaluation_tasks": top(evaluation_papers, "tasks"),
        "evaluation_metrics": top(evaluation_papers, "metrics"),
    }
    evaluation_detail.update({f"{attribute}_evaluation_count": count for attribute, count in attr_counts.items()})
    taxonomy = read_json(DATA / "taxonomy.json", {})
    session_to_macro = taxonomy.get("session_to_macro", {})
    sessions = sorted({p["normalized_session"] for p in analyzed})
    macro_stats = {macro["id"]: stats([p for p in analyzed if session_to_macro.get(p["normalized_session"]) == macro["id"]]) for macro in taxonomy.get("macros", [])}
    output = {
        "source_listed_count": manifest.get("listed_count", len(rows)),
        "data_scope": data_scope,
        "sample_paper_count": len(rows),
        "analysis_complete_count": analysis_count,
        "analysis_total_count": len(rows),
        "analysis_status": "complete" if analysis_count == len(rows) else "partial",
        "all": stats(analyzed),
        "categories": {session: stats([p for p in analyzed if p["normalized_session"] == session]) for session in sessions},
        "macro_categories": macro_stats,
        "special": {"dataset": {**stats(dataset_papers), **dataset_detail}, "evaluation": {**stats(evaluation_papers), **evaluation_detail}},
    }
    write_json(DATA / "processed_papers.json", rows)
    write_json(DATA / "stats.json", output)
    return output

def narrative_stats_hash(values):
    source = {key: value for key, value in values.items() if key not in {"unique_task_count", "unique_dataset_count", "unique_language_count"}}
    return hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def generate_narratives():
    key = os.getenv("OPENAI_API_KEY")
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("OPENAI_MODEL")
    stats = read_json(DATA / "stats.json", {})
    if not stats: raise SystemExit("请先运行 stats 生成程序统计。")
    path = DATA / "category_analysis.json"
    cache = read_json(path, {})
    errors = []
    entries = {"all": stats["all"], **{"category:" + key: value for key, value in stats["categories"].items()}, **{"macro:" + key: value for key, value in stats["macro_categories"].items()}, "special:dataset": stats["special"]["dataset"], "special:evaluation": stats["special"]["evaluation"]}
    schema = {"type":"object","required":["overview","research_hotspots","major_results","trend_summary"],"additionalProperties":False,"properties":{"overview":{"type":"string"},"research_hotspots":{"type":"array","items":{"type":"string"}},"major_results":{"type":"array","items":{"type":"string"}},"trend_summary":{"type":"string"}}}
    for name, values in entries.items():
        digest = narrative_stats_hash(values)
        if cache.get(name, {}).get("source_hash") == digest: continue
        if name.startswith("macro:") and values.get("sample_paper_count") == 0:
            cache[name] = {"overview":"当前开发样本未覆盖该研究方向，样本统计不足以判断热点或趋势。","research_hotspots":[],"major_results":[],"trend_summary":"当前开发样本未覆盖该方向，暂不作趋势判断。","source_hash":digest}
            continue
        if not key or not model:
            errors.append({"scope":name,"reason":"MODEL CREDENTIAL REQUIRED; existing narrative retained until its stats source hash is refreshed"})
            continue
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
            cache[name] = {**result,"source_hash":digest}
        except Exception as error:
            errors.append({"scope":name,"reason":str(error)})
    write_json(path,cache)
    write_json(LOGS / "category_analysis_errors.json", errors)

def validate(papers, stats):
    manifest=read_json(RAW/"crawl_manifest.json",{}); ids=[p.get("paper_id") for p in papers]
    analyses=read_analyses()
    complete = sum(all(p.get("analysis",{}).get(f) for f in FIELDS) for p in papers)
    pdf_ok = sum((CACHE/"pdfs"/(p["paper_id"]+".pdf")).exists() for p in papers)
    text_ok = sum((CACHE/"text"/(p["paper_id"]+".txt")).exists() for p in papers)
    doi_values=[p.get("doi", "").lower() for p in papers if p.get("doi")]
    titles=[p.get("title", "").casefold() for p in papers if p.get("title")]
    taxonomy = read_json(DATA / "taxonomy.json", {})
    session_labels = read_json(DATA / "session_labels_zh.json", {})
    normalized_sessions = set(manifest.get("normalized_sessions", []))
    raw_sessions = set(manifest.get("listed_sessions", []))
    taxonomy_sessions = taxonomy.get("session_to_macro", {})
    valid_macro_ids = {m.get("id") for m in taxonomy.get("macros", [])}
    expected_raw_map = {name: read_json(DATA / "category_aliases.json", {}).get(name, name) for name in raw_sessions}
    taxonomy_raw_map = taxonomy.get("raw_session_to_normalized_session", {})
    taxonomy_valid = set(taxonomy_sessions) == normalized_sessions and len(taxonomy_sessions) == len(normalized_sessions) and all(macro_id in valid_macro_ids for macro_id in taxonomy_sessions.values()) and set(taxonomy.get("raw_sessions", [])) == raw_sessions and taxonomy_raw_map == expected_raw_map
    official_normalized = set(manifest.get("normalized_sessions", []))
    session_zh_count = sum(bool(session_labels.get(name, "").strip()) for name in official_normalized)
    session_zh_missing = len(official_normalized) - session_zh_count
    paper_session_zh_count = sum(bool(p.get("session_zh")) and p.get("session_zh") == session_labels.get(p.get("normalized_session"), "") for p in papers)
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
    report={"official_listed_count":manifest.get("listed_count",0),"sample_size":len(papers),"parsed_papers":len(papers),"metadata_failures":manifest.get("errors",0),"missing_from_details":manifest.get("listed_count",0)-len(papers),"unique_paper_ids":len(set(ids)),"duplicate_paper_ids":len(ids)-len(set(ids)),"missing_titles":sum(not p.get("title") for p in papers),"missing_authors":sum(not p.get("authors") for p in papers),"missing_abstracts":sum(not p.get("abstract") for p in papers),"missing_isca_urls":sum(not p.get("isca_page_url") for p in papers),"duplicate_dois":len(doi_values)-len(set(doi_values)),"duplicate_titles":len(titles)-len(set(titles)),"raw_session_count":len({p.get("original_session") for p in papers}),"official_raw_session_count":len(manifest.get("listed_sessions", [])),"normalized_session_count":len({p.get("normalized_session") for p in papers}),"official_normalized_session_count":len(manifest.get("normalized_sessions", [])),"session_alias_count":manifest.get("session_alias_count",0),"session_normalization_pass":manifest.get("session_normalization_pass",False) and all(p.get("normalized_session") for p in papers),"taxonomy_macro_count":len(taxonomy.get("macros", [])),"taxonomy_session_count":len(taxonomy_sessions),"taxonomy_coverage_pass":taxonomy_valid,"analysis_schema_valid_count":schema_ok,"six_dimension_complete":complete,"analysis_source_full_text":sum(p.get("analysis_source")=="full_text" for p in papers),"analysis_source_abstract":sum(p.get("analysis_source")=="abstract" for p in papers),"structured_extraction_complete":sum(all(isinstance(analyses.get(p["paper_id"],{}).get("structured",{}).get(k),list) for k in ARRAY_FIELDS) for p in papers),"special_judgment_complete":sum(all(isinstance(analyses.get(p["paper_id"],{}).get("special_tags",{}).get(k,{}).get("relevant"),bool) and analyses.get(p["paper_id"],{}).get("special_tags",{}).get(k,{}).get("confidence") in ("high","medium","low") for k in ("dataset","evaluation")) for p in papers),"evidence_trace_complete":evidence_ok,"evidence_items_total":sum(len(analyses.get(p["paper_id"],{}).get("evidence",[])) for p in papers),"dataset_special_count":stats["special"]["dataset"]["paper_count"],"evaluation_special_count":stats["special"]["evaluation"]["paper_count"],"stats_count_matches":stats["all"]["paper_count"]==len(papers),"pdf_success_count":pdf_ok,"pdf_failure_count":len(papers)-pdf_ok,"full_text_success_count":text_ok,"full_text_failure_count":len(papers)-text_ok,"analysis_block_reason":"FULL ANALYSIS BLOCKED: MODEL ACCESS REQUIRED" if stats.get("analysis_status")=="blocked" else ""}
    report.update({"session_zh_count":session_zh_count,"session_zh_missing":session_zh_missing,"paper_session_zh_count":paper_session_zh_count,"session_labels_pass":session_zh_missing==0 and paper_session_zh_count==len(papers)})
    public_dir = ROOT / "site" / "data"
    public_index = read_json(public_dir / "papers-index.json", [])
    details_dir = public_dir / "paper-details"
    public_details = {path.stem: read_json(path, {}) for path in details_dir.glob("*.json")} if details_dir.exists() else {}
    def has_private_quote(value):
        if isinstance(value, dict):
            return any(key in ("evidence", "evidence_text") or has_private_quote(item) for key, item in value.items())
        if isinstance(value, list): return any(has_private_quote(item) for item in value)
        return False
    macro_narratives = read_json(DATA / "category_analysis.json", {})
    macro_hashes = {"macro:" + macro_id: narrative_stats_hash(value) for macro_id, value in stats["macro_categories"].items()}
    stat_entries = [stats["all"], *stats["categories"].values(), *stats["macro_categories"].values(), *stats["special"].values()]
    analyzed_count = stats.get("analysis_complete_count", 0)
    listed_count = max(1, stats.get("source_listed_count", len(papers)))
    sample_stats_pass = all(entry.get("coverage_percentage") == round(100 * analyzed_count / listed_count, 2) and entry.get("sample_share_percentage") == round(100 * entry.get("sample_paper_count", 0) / max(1, analyzed_count), 2) and (stats.get("data_scope") == "full" or "conference_share_percentage" not in entry) for entry in stat_entries)
    app_js = (ROOT / "site" / "assets" / "app.js").read_text(encoding="utf-8")
    style_css = (ROOT / "site" / "assets" / "style.css").read_text(encoding="utf-8")
    index_html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
    route_supported = "view.type === 'paper'" in app_js and "data/paper-details/" in app_js and "renderPaper" in app_js
    correct_detail_ids = sum(public_details.get(paper_id, {}).get("paper_id") == paper_id and (details_dir / f"{paper_id}.json").exists() for paper_id in ids)
    macro_narratives_complete = sum(macro_narratives.get(key, {}).get("source_hash") == digest and all(isinstance(macro_narratives.get(key, {}).get(field), expected) for field, expected in (("overview", str), ("research_hotspots", list), ("major_results", list), ("trend_summary", str))) for key, digest in macro_hashes.items())
    narrative_entries = {"all": stats["all"], **{"category:" + key: value for key, value in stats["categories"].items()}, **{"macro:" + key: value for key, value in stats["macro_categories"].items()}, **{"special:" + key: value for key, value in stats["special"].items()}}
    major_results_rendered = "narrative.major_results?.length" in app_js and all(isinstance(macro_narratives.get(key, {}).get("major_results"), list) for key in narrative_entries)
    processed = {paper["paper_id"]: paper for paper in read_json(DATA / "processed_papers.json", [])}
    index_by_id = {paper.get("paper_id"): paper for paper in public_index}
    def alias_pass(alias_file, field, detail_field=None):
        aliases = read_json(DATA / alias_file, {})
        cases = []
        for paper_id, analysis in analyses.items():
            original = analysis.get("structured", {}).get(field, [])
            current = processed.get(paper_id, {}).get("structured", {}).get(field, [])
            details = processed.get(paper_id, {}).get("structured", {}).get(detail_field, []) if detail_field else []
            for source, target in aliases.items():
                if source in original:
                    cases.append(target in current and source not in current and (not detail_field or source in details))
        return bool(cases) and all(cases)
    language_alias_pass = alias_pass("language_aliases.json", "languages", "language_details")
    modality_alias_pass = alias_pass("modality_aliases.json", "modalities")
    required_search = all(entry.get("search_text") and entry.get("tldr_cn") for entry in public_index)
    for paper_id, paper in processed.items():
        index_entry = index_by_id.get(paper_id, {})
        authors = paper.get("authors", [])
        search_terms = [paper.get("title", ""), *authors] if isinstance(authors, list) else [paper.get("title", ""), authors]
        search_terms.extend([index_entry.get("macro", ""), paper.get("normalized_session", ""), paper.get("original_session", ""), paper.get("tldr_cn", "")])
        search_terms.extend(paper.get("structured", {}).get(field, []) for field in ARRAY_FIELDS)
        search_terms.extend([paper.get("analysis", {}).get(field, "") for field in FIELDS])
        flattened = [value for group in search_terms for value in (group if isinstance(group, list) else [group]) if isinstance(value, str) and value]
        index_text = index_entry.get("search_text", "").casefold()
        required_search = required_search and all(value.casefold() in index_text for value in flattened)
    mobile_drawer_present = all(token in index_html for token in ("data-menu", "data-backdrop", "data-drawer-close")) and "drawer-open" in style_css
    favorites_detail_sync = all(token in app_js for token in ("FAVORITES_KEY", "data-fav", "function detailPage", "function paperCard"))
    back_forward_pass = "hashchange" in app_js and "location.hash" in app_js and "renderNav(); render();" in app_js
    report.update({
        "tldr_complete": sum(bool(p.get("tldr_cn")) for p in papers),
        "paper_detail_route_count": sum(route_supported and paper_id in public_details for paper_id in ids),
        "paper_detail_direct_refresh": route_supported,
        "paper_detail_correct_id": correct_detail_ids,
        "paper_detail_payload_count": len(public_details),
        "paper_detail_ids_match": set(public_details) == set(ids),
        "paper_detail_six_dimensions_complete": sum(all(public_details.get(paper_id, {}).get("analysis", {}).get(field) for field in FIELDS) for paper_id in ids),
        "lazy_detail_split_pass": len(public_index) == len(papers) and all("abstract" not in item and not has_private_quote(item) for item in public_index) and all(bool(public_details.get(paper_id, {}).get("abstract")) for paper_id in ids),
        "detail_lazy_load_pass": route_supported and "item.dataset.paperMore" in app_js and "item.dataset.abstract" in app_js and all("abstract" not in item for item in public_index),
        "public_payload_no_raw_evidence": not has_private_quote(public_index) and not any(has_private_quote(detail) for detail in public_details.values()),
        "evidence_locations_only": all(all(set(location).issubset({"claim_type", "field", "page_or_section", "source"}) for location in detail.get("evidence_locations", [])) for detail in public_details.values()),
        "sample_percentage_pass": stats["all"].get("sample_share_percentage") == 100 and sample_stats_pass,
        "sample_percentage_denominator_correct": sample_stats_pass,
        "language_alias_normalization_pass": language_alias_pass,
        "modality_alias_normalization_pass": modality_alias_pass,
        "papers_index_size": (public_dir / "papers-index.json").stat().st_size if (public_dir / "papers-index.json").exists() else 0,
        "major_results_rendered": major_results_rendered,
        "macro_narrative_complete": macro_narratives_complete == len(macro_hashes),
        "dataset_stats_contract_pass": all(key in stats["special"]["dataset"] for key in ("multilingual_paper_count", "low_resource_paper_count", "endangered_language_paper_count", "language_distribution", "modality_distribution", "dataset_role_distribution", "task_distribution", "availability_distribution", "new_dataset_count", "expanded_dataset_count", "reannotated_dataset_count", "curated_dataset_count")),
        "evaluation_stats_contract_pass": all(key in stats["special"]["evaluation"] for key in ("automatic_evaluation_count", "human_evaluation_count", "both_evaluation_count", "objective_evaluation_count", "subjective_evaluation_count", "benchmark_paper_count", "metric_paper_count", "protocol_paper_count", *[f"{attribute}_evaluation_count" for attribute in EVALUATION_ATTRIBUTES])),
        "methods_split_pass": "top_method_families" in stats["all"] and "top_specific_methods" in stats["all"] and "top_methods" not in stats["all"],
        "global_search_pass": required_search and "#search/" in app_js and "function searchText" in app_js,
        "mobile_drawer_present": mobile_drawer_present,
        "favorites_detail_sync": favorites_detail_sync,
        "back_forward_navigation_pass": back_forward_pass,
        "macro_narratives_with_current_stats": macro_narratives_complete,
        "macro_narrative_expected_count": len(macro_hashes),
        "local_evidence_store_count": sum(bool(a.get("evidence")) for a in analyses.values()),
    })
    report["metadata_sample_pass"] = report["sample_size"]==15 and not any(report[k] for k in ("missing_titles","missing_authors","missing_abstracts","missing_isca_urls","duplicate_paper_ids"))
    report["pdf_sample_pass"] = pdf_ok==15
    report["full_text_sample_pass"] = text_ok==15
    report["analysis_sample_pass"] = complete==15
    report["tldr_sample_pass"] = report["tldr_complete"]==15
    report["structured_sample_pass"] = report["structured_extraction_complete"]==15
    report["special_judgment_sample_pass"] = report["special_judgment_complete"]==15
    report["category_count_sum"] = sum(x["paper_count"] for x in stats["categories"].values())
    report["category_count_sum_matches"] = report["category_count_sum"]==len(papers)
    report["stats_sample_pass"] = report["stats_count_matches"] and report["category_count_sum_matches"] and stats["all"]["paper_count"]==15 and report["structured_extraction_complete"]==15
    report["public_detail_sample_pass"] = report["paper_detail_route_count"]==15 and report["paper_detail_correct_id"]==15 and report["paper_detail_payload_count"]==15 and report["paper_detail_ids_match"] and report["paper_detail_six_dimensions_complete"]==15 and report["lazy_detail_split_pass"] and report["detail_lazy_load_pass"] and report["public_payload_no_raw_evidence"] and report["evidence_locations_only"]
    report["macro_narrative_sample_pass"] = report["macro_narratives_with_current_stats"]==report["macro_narrative_expected_count"]
    report["product_ui_sample_pass"] = all(report[key] for key in ("paper_detail_direct_refresh", "major_results_rendered", "language_alias_normalization_pass", "modality_alias_normalization_pass", "global_search_pass", "mobile_drawer_present", "favorites_detail_sync", "back_forward_navigation_pass"))
    report["session_merge_pass"] = report["session_normalization_pass"] and report["session_alias_count"] == len(read_json(DATA / "category_aliases.json", {}))
    report["session_sample_pass"] = report["session_normalization_pass"] and report["session_labels_pass"]
    report["card_details_expand_pass"] = "data-paper-more" in app_js and "function paperMore" in app_js
    report["sample_pass"] = all(report[k] for k in ("metadata_sample_pass","pdf_sample_pass","full_text_sample_pass","analysis_sample_pass","tldr_sample_pass","structured_sample_pass","special_judgment_sample_pass","stats_sample_pass","public_detail_sample_pass","macro_narrative_sample_pass","sample_percentage_pass","dataset_stats_contract_pass","evaluation_stats_contract_pass","methods_split_pass","product_ui_sample_pass","session_sample_pass","card_details_expand_pass")) and report["taxonomy_coverage_pass"] and report["analysis_schema_valid_count"] == len(papers) and report["evidence_trace_complete"] == len(papers)
    report["overall_pass"] = report["sample_pass"] if manifest.get("sample_limit") else report["sample_pass"] and report["missing_from_details"]==0
    report["incomplete_analyses"] = [{"paper_id":p["paper_id"],"title":p["title"],"reason":"model credentials unavailable"} for p in papers if not all(p.get("analysis",{}).get(f) for f in FIELDS)]
    write_json(DATA/"validation_report.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report

def build_site(papers, stats):
    site = ROOT / "site"
    (site / "assets").mkdir(parents=True, exist_ok=True)
    data_dir = site / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    detail_dir = data_dir / "paper-details"
    detail_dir.mkdir(parents=True, exist_ok=True)
    taxonomy = read_json(DATA / "taxonomy.json", {})
    session_labels = read_json(DATA / "session_labels_zh.json", {})
    session_to_macro = taxonomy.get("session_to_macro", {})
    macro_names = {macro["id"]: macro["name"] for macro in taxonomy.get("macros", [])}
    index = []
    expected_details = set()
    for paper in papers:
        macro_id = session_to_macro.get(paper.get("normalized_session"), "")
        macro_name = macro_names.get(macro_id, "")
        session_zh = session_labels.get(paper.get("normalized_session", ""), "")
        analysis = copy.deepcopy(paper.get("analysis", {}))
        structured = copy.deepcopy(paper.get("structured", {}))
        authors = paper.get("authors", "")
        author_text = ", ".join(authors) if isinstance(authors, list) else authors
        topics = structured.get("topics", [])[:5]
        search_terms = [value for values in structured.values() if isinstance(values, list) for value in values if isinstance(value, str)]
        search_text = " ".join([paper.get("title", ""), author_text, paper.get("original_session", ""), paper.get("normalized_session", ""), session_zh, macro_name, paper.get("tldr_cn", ""), *search_terms, *[analysis.get(field, "") for field in FIELDS]])
        index.append({
            "paper_id": paper["paper_id"], "title": paper.get("title", ""), "authors": authors,
            "macro_id": macro_id, "macro_name": macro_name, "macro": macro_name,
            "normalized_session": paper.get("normalized_session", ""), "original_session": paper.get("original_session", ""), "session_zh": session_zh,
            "tldr_cn": paper.get("tldr_cn", ""), "analysis": analysis, "topics": topics,
            "special_tags": copy.deepcopy(paper.get("special_tags", {})), "pdf_url": paper.get("pdf_url", ""),
            "isca_page_url": paper.get("isca_page_url", ""), "doi": paper.get("doi", ""), "search_text": search_text,
        })
        details = copy.deepcopy(paper)
        evidence_locations = [{key: item.get(key, "") for key in ("claim_type", "field", "page_or_section", "source")} for item in details.pop("evidence", [])]
        for field in ("dataset_entities", "evaluation_entities"):
            for entity in details.get(field, []): entity.pop("evidence", None)
        details.update(macro_id=macro_id, macro_name=macro_name, macro=macro_name, evidence_locations=evidence_locations)
        details["session_zh"] = session_zh
        details.pop("detail_http_status", None)
        paper_id = paper["paper_id"]
        expected_details.add(paper_id)
        write_json(detail_dir / f"{paper_id}.json", details)
    for old_detail in detail_dir.glob("*.json"):
        if old_detail.stem not in expected_details: old_detail.unlink()
    # Remove the previous monolithic public payload, which included quote-level evidence.
    legacy_payload = data_dir / "papers.json"
    if legacy_payload.exists(): legacy_payload.unlink()
    write_json(data_dir / "papers-index.json", index)
    write_json(data_dir / "stats.json", stats)
    write_json(data_dir / "category_analysis.json", read_json(DATA / "category_analysis.json", {}))
    write_json(data_dir / "taxonomy.json", taxonomy)
    write_json(data_dir / "session_labels_zh.json", session_labels)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("command",choices=["crawl","crawl-official","normalize","pdf","extract","analyze","analyze-official","narratives","stats","build","validate","sample"]); ap.add_argument("--limit",type=int)
    args=ap.parse_args(); papers=read_json(DATA/"papers.json",[])
    if args.command=="crawl-official":
        official_path=RAW/"official_papers_metadata.json"
        official_manifest=RAW/"official_crawl_manifest.json"
        official_errors=LOGS/"official_crawl_errors.json"
        papers=crawl(output_path=official_path,manifest_path=official_manifest,errors_path=official_errors)
        papers=normalize_sessions(papers,manifest_path=official_manifest,output_path=official_path)
        print(json.dumps({"official_count":read_json(official_manifest,{}).get("listed_count",0),"parsed_count":len(papers),"errors":read_json(official_manifest,{}).get("errors",0),"metadata_path":str(official_path),"manifest_path":str(official_manifest)},ensure_ascii=False,indent=2))
    if args.command=="crawl": papers=crawl(args.limit); papers=normalize_sessions(papers)
    if args.command=="sample": papers=crawl(args.limit or 15); papers=normalize_sessions(papers)
    if args.command in ("pdf","sample"): fetch_pdfs(papers,args.limit or (15 if args.command=="sample" else None))
    if args.command in ("extract","sample"): extract_text(papers,args.limit or (15 if args.command=="sample" else None))
    if args.command=="normalize": papers=normalize_sessions(papers)
    if args.command=="analyze": analyze(args.limit)
    if args.command=="analyze-official": analyze(args.limit,RAW/"official_papers_metadata.json")
    if args.command=="narratives": generate_narratives()
    if args.command in ("stats","build","validate"):
        stats=aggregate(papers)
        processed=read_json(DATA/"processed_papers.json",papers)
        if args.command in ("build","validate"): build_site(processed,stats)
        if args.command=="validate": validate(processed,stats)
    if args.command=="sample":
        sample_cached = sum((CACHE / "pdfs" / (p["paper_id"] + ".pdf")).exists() for p in papers[:args.limit or 15])
        print(f"Sample metadata={len(papers)}; PDFs cached={sample_cached}")

if __name__=="__main__": main()
