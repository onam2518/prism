"""표 입력(xlsx/csv/jsonl) 적재 + 필드 자동 매핑 + 적용 가능 판정.

컬럼명이 우리 스키마(displayServiceName·title·subtitle·body)와 달라도,
별칭 사전으로 필요한 값을 추론한다. 핵심(title+body)이 잡히면 '가능', 아니면 '불가능'.
의존성 0: xlsx 는 zipfile+xml(표준 라이브러리)로 직접 파싱.
"""
from __future__ import annotations
import csv
import json
import os

# 필드별 별칭(소문자 비교, 한/영). 위에서부터 우선.
ALIASES = {
    "title": ["title", "제목", "헤드라인", "headline", "표제", "글제목", "제목명",
              "subject", "타이틀", "head"],
    "body": ["body", "본문", "본문내용", "기사본문", "기사내용", "content", "contents",
             "내용", "내용본문", "text", "article", "기사", "description", "desc", "원문"],
    "subtitle": ["subtitle", "부제목", "부제", "summary", "요약", "subhead", "lead", "리드"],
    "displayServiceName": ["displayservicename", "콘텐츠그룹", "서비스명", "service", "서비스", "구분",
                           "채널", "channel", "category", "카테고리", "매체", "섹션",
                           "section", "source", "type", "지면"],
    # 참조용 원문 링크(선택). 있으면 상세뷰 '원문 열기' 로 연결.
    "source_url": ["sourceurl", "url", "link", "permalink", "href", "링크", "원문링크",
                   "원문url", "articleurl", "weburl", "원문주소", "주소", "originurl"],
}
REQUIRED = ["title", "body"]            # 이 둘이 잡혀야 '가능'
OPTIONAL_DEFAULT = {"subtitle": "", "displayServiceName": "", "source_url": ""}


def read_table(path: str) -> tuple[list, list]:
    """파일 → (headers, rows[dict]). xlsx/csv/tsv/jsonl/json 지원."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return _read_xlsx(path)
    if ext in (".csv", ".tsv"):
        return _read_csv(path, "\t" if ext == ".tsv" else ",")
    if ext in (".jsonl", ".ndjson"):
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        rows = [r.get("content", r) for r in rows]
        return (list(rows[0].keys()) if rows else []), rows
    if ext == ".json":
        data = json.load(open(path, encoding="utf-8"))
        rows = data if isinstance(data, list) else [data]
        return (list(rows[0].keys()) if rows else []), rows
    raise ValueError(f"지원하지 않는 형식: {ext} (xlsx/csv/tsv/jsonl/json)")


def _read_csv(path, delim):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter=delim)
        rows = [dict(r) for r in rd]
    return (rd.fieldnames or []), rows


# xlsx(=zip) 멤버 압축해제 상한(zip bomb 방어): 중앙 디렉터리가 선언한 uncompressed 크기가
# 이 값을 넘으면 읽지 않는다(정상 스프레드시트는 훨씬 작다 · 50KB 업로드→52MB 전개 실증 방어).
_MAX_XLSX_MEMBER = 48 * 1024 * 1024


# xlsx: zip + xml (표준 라이브러리)
def _read_xlsx(path):
    import zipfile
    import xml.etree.ElementTree as ET

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    def read_capped(z, name):
        info = z.getinfo(name)
        if info.file_size > _MAX_XLSX_MEMBER:
            raise ValueError(f"xlsx 멤버 과대({info.file_size} bytes) · 압축폭탄 의심")
        return z.read(name)

    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(read_capped(z, "xl/sharedStrings.xml"))
            for si in root:
                shared.append("".join(t.text or "" for t in si.iter()
                                      if local(t.tag) == "t"))
        sheet = next((n for n in z.namelist()
                      if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")), None)
        if not sheet:
            return [], []
        root = ET.fromstring(read_capped(z, sheet))
        grid = []
        for row in root.iter():
            if local(row.tag) != "row":
                continue
            cells = {}
            for c in row:
                if local(c.tag) != "c":
                    continue
                ref = c.get("r", "")
                col = "".join(ch for ch in ref if ch.isalpha())
                t = c.get("t", "")
                val = ""
                for ch in c:
                    if local(ch.tag) == "v":
                        val = ch.text or ""
                    elif local(ch.tag) == "is":
                        val = "".join(x.text or "" for x in ch.iter() if local(x.tag) == "t")
                if t == "s" and val.isdigit():
                    val = shared[int(val)] if int(val) < len(shared) else ""
                cells[_col_idx(col)] = val
            grid.append(cells)
    if not grid:
        return [], []
    width = max((max(c) + 1 if c else 0) for c in grid)
    headers = [grid[0].get(i, f"col{i}") for i in range(width)]
    rows = []
    for cells in grid[1:]:
        if not any((cells.get(i) or "").strip() for i in range(width)):
            continue
        rows.append({headers[i]: cells.get(i, "") for i in range(width)})
    return headers, rows


def _col_idx(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


# 필드 매핑 추론 + 판정
def infer_mapping(headers: list, override: dict = None) -> dict:
    """헤더 → {우리필드: 원본컬럼}. override 로 강제 지정 가능."""
    norm = {h: str(h).strip().lower().replace(" ", "").replace("_", "") for h in headers}
    mapping = {}
    used = set()                                  # 한 컬럼이 두 필드에 중복배정되지 않게
    for field, aliases in ALIASES.items():
        if override and field in override:
            mapping[field] = override[field]
            used.add(override[field])
            continue
        hit = None
        for al in aliases:                       # 정확 일치 우선
            for h, nh in norm.items():
                if h not in used and nh == al:
                    hit = h
                    break
            if hit:
                break
        if not hit:                              # 헤더가 별칭을 '포함'할 때만(역방향 금지)
            for al in aliases:
                for h, nh in norm.items():
                    if h not in used and al in nh:
                        hit = h
                        break
                if hit:
                    break
        if hit:
            mapping[field] = hit
            used.add(hit)
    return mapping


def assess(path: str, override: dict = None) -> dict:
    """적용 가능 판정. {ok, mapping, missing, headers, n_rows, samples, reason}."""
    headers, rows = read_table(path)
    mapping = infer_mapping(headers, override)
    missing = [f for f in REQUIRED if f not in mapping]
    ok = not missing
    reason = ("핵심 필드(제목·본문) 매핑 성공 → 적용 가능"
              if ok else f"필수 필드 미발견: {', '.join(missing)} → 컬럼명을 --map 으로 지정 필요")
    samples = []
    if ok:
        for r in rows[:2]:
            samples.append({k: str(r.get(v, ""))[:40] for k, v in mapping.items()})
    return {"ok": ok, "mapping": mapping, "missing": missing, "headers": headers,
            "n_rows": len(rows), "samples": samples, "reason": reason}


def to_contents_rows(rows: list, override: dict = None) -> tuple:
    """JSON 레코드(list[dict]) → (콘텐츠 리스트, 매핑). API 인입용. 판정 불가면 ValueError."""
    headers = list(rows[0].keys()) if rows else []
    m = infer_mapping(headers, override)
    missing = [f for f in REQUIRED if f not in m]
    if missing:
        raise ValueError(f"필수 필드 미발견: {', '.join(missing)} (헤더: {', '.join(map(str, headers))[:200]})")
    out = []
    for r in rows:
        item = dict(OPTIONAL_DEFAULT)
        for field, col in m.items():
            item[field] = str(r.get(col, "") or "")
        out.append(item)
    return out, m


def to_contents(path: str, override: dict = None) -> list:
    """표 → 우리 콘텐츠 스키마 리스트. 판정 불가면 ValueError."""
    a = assess(path, override)
    if not a["ok"]:
        raise ValueError(a["reason"])
    headers, rows = read_table(path)
    m = a["mapping"]
    out = []
    for r in rows:
        item = dict(OPTIONAL_DEFAULT)
        for field, col in m.items():
            item[field] = str(r.get(col, "") or "")
        out.append(item)
    return out


def parse_map(s: str) -> dict:
    """--map "title=제목,body=내용" → {title:제목, body:내용}."""
    if not s:
        return {}
    out = {}
    for pair in s.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out
