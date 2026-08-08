# -*- coding: utf-8 -*-
"""외부 서지 DB 호출 공통 유틸 — 재시도·백오프와 '일시 오류' 구분.

일시 오류(네트워크 끊김·429·5xx)를 '문헌 없음'과 섞으면, 실제로 존재하는 문헌이
'미발견'으로 표시되어 편집위원이 멀쩡한 참고문헌을 의심하게 된다.
이 모듈은 그 둘을 예외로 갈라 놓는다 — 찾지 못하면 None, 확인하지 못하면 예외.

verify.py(해외 DB)와 verify_kr.py(국내 DB)가 같은 규약을 쓰도록 여기에 모았다.
"""
import time

import httpx


class LookupUnavailable(Exception):
    """외부 DB 일시 오류(429·5xx·네트워크) — '미발견'과 구분하기 위한 예외."""


DEFAULT_TIMEOUT = 12
DEFAULT_TRIES = 3      # 최초 1회 + 재시도 2회
MAX_WAIT = 4.0         # 한 번의 대기 상한(초) — 사용자 대기 시간이 늘어지지 않도록


def _wait_for(resp: httpx.Response | None, attempt: int) -> float:
    """다음 재시도까지의 대기 — 서버가 Retry-After를 주면 그 값을, 아니면 지수 백오프."""
    if resp is not None:
        raw = resp.headers.get("retry-after", "")
        try:
            return min(MAX_WAIT, max(0.5, float(raw)))
        except (TypeError, ValueError):
            pass
    return min(MAX_WAIT, 1.0 * (2 ** attempt))


def get_with_retry(client: httpx.Client, url: str, *, params=None, headers=None,
                   timeout: float = DEFAULT_TIMEOUT,
                   tries: int = DEFAULT_TRIES) -> httpx.Response:
    """GET + 429/5xx·네트워크 오류 재시도. 끝내 실패하면 LookupUnavailable.

    4xx(429 제외)는 재시도하지 않고 그대로 돌려준다 — 질의가 잘못된 경우이므로
    다시 보내도 결과가 같고, 이는 '없음'으로 해석해야 할 응답이다.
    """
    last = ""
    for attempt in range(max(1, tries)):
        try:
            resp = client.get(url, params=params, headers=headers, timeout=timeout)
        except httpx.HTTPError as ex:
            last = str(ex) or ex.__class__.__name__
            if attempt < tries - 1:
                time.sleep(_wait_for(None, attempt))
                continue
            raise LookupUnavailable(last)
        if resp.status_code == 429 or resp.status_code >= 500:
            last = f"HTTP {resp.status_code}"
            if attempt < tries - 1:
                time.sleep(_wait_for(resp, attempt))
                continue
            raise LookupUnavailable(last)
        return resp
    raise LookupUnavailable(last or "요청 실패")
