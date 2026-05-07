from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicJobItem:
    title: str
    organization: str
    url: str | None
    deadline: str | None


def _http_get_json(url: str, timeout_sec: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    return json.loads(raw)


def fetch_public_jobs_from_data_go_kr() -> list[PublicJobItem]:
    """
    공공데이터포털 API는 **발급받은 인증키가 필요**합니다.
    - 환경변수 `DATA_GO_KR_SERVICE_KEY`가 없으면 빈 리스트를 반환합니다.
    - 실제 응답 스키마는 API별로 다를 수 있어, 최소한의 범용 파서만 제공합니다.
    """
    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY")
    if not service_key:
        return []

    # 재정경제부_공공기관 채용정보 조회서비스 (예시, API 변경 가능)
    # https://www.data.go.kr/data/15125273/openapi.do
    # NOTE: 실제 endpoint는 포털에서 확인 필요. 여기서는 "키가 있을 때만" 확장 가능한 형태로 둡니다.
    base = os.getenv("DATA_GO_KR_PUBLIC_RECRUIT_URL", "").strip()
    if not base:
        return []

    qs = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "pageNo": "1",
            "numOfRows": "100",
            "type": "json",
        },
        doseq=True,
        safe="%:/?=&",
    )
    url = f"{base}?{qs}"
    try:
        data = _http_get_json(url)
    except Exception:
        return []

    # best-effort schema
    items = []
    body = data.get("response", {}).get("body", {})
    rows = body.get("items") or body.get("item") or []
    if isinstance(rows, dict):
        rows = rows.get("item") or []
    if not isinstance(rows, list):
        return []

    for r in rows:
        if not isinstance(r, dict):
            continue
        title = str(r.get("recrtTitle") or r.get("title") or "").strip()
        org = str(r.get("instNm") or r.get("orgName") or r.get("organization") or "").strip()
        link = r.get("recrtUrl") or r.get("url") or r.get("link")
        deadline = str(r.get("pbancEndYmd") or r.get("endDate") or r.get("deadline") or "").strip() or None
        if title and org:
            items.append(PublicJobItem(title=title, organization=org, url=str(link).strip() if link else None, deadline=deadline))
    return items

