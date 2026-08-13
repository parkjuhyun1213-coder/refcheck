# -*- coding: utf-8 -*-
"""서버 스모크 테스트: 상태, 기준 관리, 업로드 처리, 폴더 일괄, 다운로드."""
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765"
# 프로젝트 위치가 바뀌어도 따라오도록 이 파일 기준 상대경로로 잡는다
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "샘플원고"
SAMPLE = str(SAMPLE_DIR / "샘플논문_다문화서비스.docx")
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# 처리 요청은 이용 통계를 위해 사용자 이름·소속을 필수로 받는다
SUBMITTER = {"user_name": "스모크테스트", "org": "기타", "org_etc": "테스트"}


def env_get(name: str) -> str:
    """.env에서 값 하나를 읽는다(테스트 전용 최소 구현)."""
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip().upper() == name:
                return v.strip()
    return ""


# KCI에 실제로 등재된 논문 — 국내 문헌 검증 점검용 기준값(2026-08 실측)
KCI_SAMPLE = {"title": "일본의 다문화 공생을 위한 방재교육", "author": "이정희",
              "year": "2023", "container": "한국일본교육학연구"}
KCI_FAKE_TITLE = "존재하지않는가상의논문제목 zzqx 12345"


def check_kci(c) -> bool:
    """국내 문헌 검증(KCI) 점검. 통과 여부를 반환한다.

    바깥 서비스를 부르는 단계라 KCI가 점검할 수 없는 상태일 수 있다. 그럴 때는
    실패로 몰지 않고 경고만 남긴다 — 우리 코드 문제와 KCI 사정을 섞으면 안 된다.
    """
    if not env_get("KCI_API_KEY"):
        print("10) KCI 검증: 건너뜀 — .env에 KCI_API_KEY가 없습니다.")
        return True

    # 10-1) 관리자 화면의 'KCI 참고문헌 가져오기'
    r = c.get(BASE + "/api/admin/kci/references",
              params={"title": KCI_SAMPLE["title"], "author": KCI_SAMPLE["author"],
                      "year": KCI_SAMPLE["year"]})
    if r.status_code == 502:  # 키·허용 IP·서비스 항목 문제는 서버가 이유를 알려 준다
        print("10) KCI 검증: 경고 — KCI를 조회하지 못했습니다.", r.json().get("detail", "")[:160])
        return True
    if r.status_code != 200 or r.json().get("count", 0) < 1:
        print(f"10) KCI 참고문헌 조회 실패: HTTP {r.status_code} {r.text[:200]}")
        return False
    print("10) KCI 참고문헌 조회:", r.json()["count"], "건")

    # 10-2) 없는 논문은 404 — '조회 실패'와 '자료 없음'이 구분되는지
    r = c.get(BASE + "/api/admin/kci/references", params={"title": KCI_FAKE_TITLE})
    if r.status_code != 404:
        print(f"11) 없는 논문인데 404가 아님: HTTP {r.status_code} {r.text[:200]}")
        return False
    print("11) 없는 논문 → 404 정상")

    # 10-3) 검증 로직 자체(단건 검증은 HTTP로 노출돼 있지 않아 직접 호출)
    import httpx as _httpx
    import verify
    from http_util import LookupUnavailable
    entry = {"type": "journal", "lang": "ko", "title": KCI_SAMPLE["title"],
             "authors": [KCI_SAMPLE["author"]], "container": KCI_SAMPLE["container"],
             "year": KCI_SAMPLE["year"]}
    try:
        with _httpx.Client(headers={"User-Agent": "refstd-agent"}) as vc:
            got = verify.verify_entry(vc, entry)
            fake = verify.verify_entry(vc, {**entry, "title": KCI_FAKE_TITLE,
                                            "container": "가상학회지"})
    except LookupUnavailable as e:
        print("12) KCI 문헌 검증: 경고 — 조회 불가", e)
        return True
    ok = True
    if got.get("status") != "verified" or got.get("source") != "KCI":
        print("12) 실존 논문 검증 실패:", got.get("status"), got.get("detail")); ok = False
    # KCI가 준 등재 구분이 학술지 신뢰도에 반영되는지
    if "등재" not in ((got.get("journal") or {}).get("detail") or ""):
        print("12) 학술지 등재 구분 누락:", got.get("journal")); ok = False
    # DOI는 Crossref 조회에 바로 쓸 수 있는 순수 형태여야 한다(URL이면 철회 확인이 실패)
    if got.get("found_doi", "").startswith("http"):
        print("12) DOI가 URL 형태:", got["found_doi"]); ok = False
    if fake.get("status") == "verified":
        print("12) 없는 논문이 검증됨:", fake.get("detail")); ok = False
    if ok:
        print("12) KCI 문헌 검증:", got["detail"], "|", got["journal"]["detail"],
              "| DOI", got.get("found_doi") or "없음", "| 가짜 논문", fake.get("status"))
    return ok


# SEOJI에 실제로 등재된 단행본 — 국내 단행본 검증 점검용 기준값(2026-08 실측).
# 같은 서명으로 2008년판(이수상)과 2026년판(김선태·이수상)이 함께 있어,
# '원고 연도에 맞는 판을 고르는지'까지 이 한 건으로 확인할 수 있다.
NLK_SAMPLE = {"title": "디지털도서관 운영론", "author": "이수상",
              "year": "2008", "other_year": "2026", "publisher": "한국도서관협회"}
NLK_FAKE_TITLE = "인공지능 도서관학의 초월적 재구성 zzqx"


def check_nlk() -> bool:
    """국내 단행본 검증(국립중앙도서관 SEOJI) 점검. 통과 여부를 반환한다.

    KCI와 달리 관리자 화면에 노출된 경로가 없어 검증 함수를 직접 부른다.

    KCI와 같은 규약 — 키가 없으면 건너뛰고, SEOJI를 조회하지 못하면 경고만 남기며,
    결과가 틀렸을 때만 실패로 본다.
    """
    if not env_get("NLK_CERT_KEY"):
        print("13) 국립중앙도서관 검증: 건너뜀 — .env에 NLK_CERT_KEY가 없습니다.")
        return True

    import httpx as _httpx
    import verify
    from http_util import LookupUnavailable
    entry = {"type": "book", "lang": "ko", "title": NLK_SAMPLE["title"],
             "authors": [NLK_SAMPLE["author"]], "year": NLK_SAMPLE["year"],
             "publisher": NLK_SAMPLE["publisher"]}
    try:
        with _httpx.Client(headers={"User-Agent": "refstd-agent"}) as vc:
            got = verify.verify_entry(vc, entry)
            other = verify.verify_entry(vc, {**entry, "year": NLK_SAMPLE["other_year"]})
            fake = verify.verify_entry(vc, {**entry, "title": NLK_FAKE_TITLE})
    except LookupUnavailable as e:
        print("13) 국립중앙도서관 검증: 경고 — 조회 불가", e)
        return True

    ok = True
    if got.get("status") != "verified" or got.get("source") != "국립중앙도서관":
        print("13) 실존 단행본 검증 실패:", got.get("status"), got.get("detail")); ok = False
    # 같은 서명의 다른 판이 잡히면, 맞게 쓴 발행연도를 틀렸다고 교정하게 된다
    for want, res in ((NLK_SAMPLE["year"], got), (NLK_SAMPLE["other_year"], other)):
        y = (res.get("meta") or {}).get("year", "")
        if y != want:
            print(f"13) 원고가 {want}년인데 {y or '연도 없는'}년 판이 잡힘:",
                  (res.get("meta") or {}).get("title", "")); ok = False
    if fake.get("status") == "verified":
        print("13) 없는 단행본이 검증됨:", fake.get("detail")); ok = False

    # 단행본은 출판사가 필수 서지요소다 — 대조에 쓰이도록 meta까지 실려야 한다
    import main as srv
    meta = got.get("meta") or {}
    if meta.get("publisher") != NLK_SAMPLE["publisher"]:
        print("13) 출판사가 meta에 실리지 않음:", repr(meta.get("publisher"))); ok = False
    if not meta.get("isbn") or "ISBN" not in (got.get("detail") or ""):
        print("13) ISBN이 빠짐:", repr(meta.get("isbn")), "|", got.get("detail")); ok = False
    # 맞게 쓴 출판사에 교정을 제안하면 안 되고(법인격 표기 차이 포함),
    # 틀리게 썼을 때는 제안해야 한다
    def pub_sugg(pub, m=meta):
        return [s for s in srv._build_suggestions(
            {"type": "book", "year": NLK_SAMPLE["year"], "publisher": pub}, m)
            if s["field"] == "publisher"]
    if pub_sugg(NLK_SAMPLE["publisher"]) or pub_sugg("(주)" + NLK_SAMPLE["publisher"]):
        print("13) 맞게 쓴 출판사에 교정을 제안함"); ok = False
    if not pub_sugg("엉뚱출판사"):
        print("13) 틀린 출판사에 교정을 제안하지 않음"); ok = False
    # 학술지 논문에는 출판사 제안이 붙으면 안 된다(참고문헌에 적지 않는 항목)
    if [s for s in srv._build_suggestions(
            {"type": "journal", "year": "1993"},
            {"year": "1993", "publisher": "American Economic Association"})
            if s["field"] == "publisher"]:
        print("13) 학술지 논문에 출판사 교정을 제안함"); ok = False

    if ok:
        print("13) 국립중앙도서관 단행본 검증:", got["detail"],
              "| 판 구분", (got.get("meta") or {}).get("year"), "·",
              (other.get("meta") or {}).get("year"),
              "| 출판사", meta.get("publisher"),
              "| 가짜 단행본", fake.get("status"))
    return ok


def job_id_of(r, step: str) -> str:
    """처리 요청 응답에서 job_id를 꺼낸다. 실패 시 서버가 알려준 이유를 그대로 보여준다."""
    if r.status_code != 200 or "job_id" not in r.json():
        print(f"{step} 실패: HTTP {r.status_code} {r.text[:300]}")
        sys.exit(1)
    return r.json()["job_id"]


def wait_job(c, job_id, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"{BASE}/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(1)
    raise TimeoutError


def main():
    with httpx.Client(timeout=60) as c:
        # 서버 기동 대기 — 끝내 못 붙으면 원인을 분명히 알리고 끝낸다
        # (예전에는 여기서 응답 변수가 없는 채로 진행돼 엉뚱한 오류로 죽었다)
        r = None
        for _ in range(20):
            try:
                r = c.get(BASE + "/api/status")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        if r is None:
            print(f"서버에 접속하지 못했습니다: {BASE}\n"
                  f"먼저 실행.bat 또는 "
                  f"python -m uvicorn main:app --port 8765 로 서버를 켠 뒤 다시 실행해 주세요.")
            sys.exit(1)
        print("1) /api/status:", r.json())
        assert r.status_code == 200

        # 관리자 로그인 — 기준 관리·폴더 일괄은 관리자 권한이 필요하다
        admin_id, admin_pw = env_get("ADMIN_ID"), env_get("ADMIN_PASSWORD")
        if not admin_id or not admin_pw:
            print("`.env`에 ADMIN_ID·ADMIN_PASSWORD가 없어 관리자 기능을 점검할 수 없습니다.")
            sys.exit(1)
        r = c.post(BASE + "/api/admin/login",
                   data={"admin_id": admin_id, "password": admin_pw})
        if r.status_code != 200 or not r.json().get("ok"):
            print("관리자 로그인 실패:", r.status_code, r.text[:200],
                  "\n`.env`의 ADMIN_ID·ADMIN_PASSWORD가 서버가 쓰는 값과 같은지 확인해 주세요.")
            sys.exit(1)
        print("1-1) 관리자 로그인 OK")

        r = c.get(BASE + "/")
        assert "참고문헌" in r.text
        print("2) 메인 페이지 OK,", len(r.text), "bytes")

        r = c.get(BASE + "/api/styles").json()
        print("3) 기준 목록:", [s["name"] for s in r["styles"]])

        # 기준 추가(직접 입력) → 목록 → 삭제
        style_text = ("참고문헌 작성 기준: 저자명(발행연도), 논문제목, 학술지명 권(호), 인용면수 순으로 기재한다. "
                      "저자가 여러 명이면 모두 기재하고 쉼표로 구분한다. 국문 문헌을 먼저 배열하고 영문 문헌을 "
                      "뒤에 배열하며 각각 가나다순, 알파벳순으로 정렬한다. " * 3)
        r = c.post(BASE + "/api/styles", data={"name": "테스트학회지", "text": style_text}).json()
        print("4) 기준 추가:", r)
        assert r["ok"]
        sid = r["style"]["id"]
        r = c.delete(f"{BASE}/api/styles/{sid}").json()
        print("5) 기준 삭제:", r)

        # 업로드 처리
        with open(SAMPLE, "rb") as f:
            r = c.post(BASE + "/api/process",
                       files=[("files", ("샘플논문.docx", f,
                               "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
                       data={"style_id": "munpyeonhyeop", "verify": "0", "crosscheck": "1",
                             "english": "0", **SUBMITTER})
        job_id = job_id_of(r, "6) 업로드 처리")
        j = wait_job(c, job_id)
        res = j["results"][0]
        print("6) 업로드 처리:", j["status"], "| 요약:", res["summary"])
        assert j["status"] == "done" and res["summary"]["total"] == 12

        # DOCX/TXT/ZIP 다운로드
        r = c.get(f"{BASE}/api/jobs/{job_id}/download/0?fmt=docx")
        print("7) DOCX 다운로드:", r.status_code, len(r.content), "bytes")
        assert r.status_code == 200 and r.content[:2] == b"PK"
        r = c.get(f"{BASE}/api/jobs/{job_id}/download/0?fmt=txt")
        assert r.status_code == 200 and "참고문헌" in r.text
        r = c.get(f"{BASE}/api/jobs/{job_id}/download_zip")
        print("8) ZIP 다운로드:", r.status_code, len(r.content), "bytes")
        assert r.status_code == 200

        # 폴더 일괄 처리
        r = c.post(BASE + "/api/process_folder",
                   data={"path": str(SAMPLE_DIR), "style_id": "munpyeonhyeop",
                         "verify": "0", "crosscheck": "1", "english": "0", **SUBMITTER})
        job_id = job_id_of(r, "9) 폴더 일괄")
        j = wait_job(c, job_id)
        print("9) 폴더 일괄:", j["status"], "| 파일:", j["done_files"], "| 저장:", j["output_dir"])
        assert j["status"] == "done"

        # 국내 문헌 검증(KCI) — 업로드 점검은 verify=0이라 이 경로를 지나지 않는다
        if not check_kci(c):
            print("\n== KCI 검증 점검 실패 ==")
            sys.exit(1)

        # 국내 단행본 검증(국립중앙도서관)
        if not check_nlk():
            print("\n== 국립중앙도서관 검증 점검 실패 ==")
            sys.exit(1)

        print("\n== 서버 스모크 테스트 전체 통과 ==")


if __name__ == "__main__":
    main()
