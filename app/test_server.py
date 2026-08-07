# -*- coding: utf-8 -*-
"""서버 스모크 테스트: 상태, 기준 관리, 업로드 처리, 폴더 일괄, 다운로드."""
import time
import httpx

BASE = "http://127.0.0.1:8765"
SAMPLE = r"c:\apps\writing_reference\샘플원고\샘플논문_다문화서비스.docx"


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
        # 서버 기동 대기
        for _ in range(20):
            try:
                r = c.get(BASE + "/api/status")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        print("1) /api/status:", r.json())
        assert r.status_code == 200

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
                       data={"style_id": "munpyeonhyeop", "verify": "0", "crosscheck": "1", "english": "0"})
        job_id = r.json()["job_id"]
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
                   data={"path": r"c:\apps\writing_reference\샘플원고",
                         "style_id": "munpyeonhyeop", "verify": "0", "crosscheck": "1", "english": "0"})
        job_id = r.json()["job_id"]
        j = wait_job(c, job_id)
        print("9) 폴더 일괄:", j["status"], "| 파일:", j["done_files"], "| 저장:", j["output_dir"])
        assert j["status"] == "done"

        print("\n== 서버 스모크 테스트 전체 통과 ==")


if __name__ == "__main__":
    main()
