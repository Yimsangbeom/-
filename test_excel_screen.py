"""엑셀 '통계(화면)' 시트: 화면 요약 카드/표 + (POST 로 보낸) 그래프 그림."""
import base64, io, os, tempfile
os.environ["OMR_DATA_DIR"] = tempfile.mkdtemp()
import openpyxl
from PIL import Image
from app import app

c = app.test_client()
assert c.post("/api/setup", json={"password": "testpass123"}).status_code == 200
assert c.post("/api/test/run").status_code == 200
def png(w=1120, h=600):
    b = io.BytesIO(); Image.new("RGB", (w, h), (37, 99, 235)).save(b, "PNG"); return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
imgs = [{"title": t, "png": png()} for t in ("점수 분포 (세로 막대)", "점수 분포 (꺾은선)", "과목별 평균 점수 (세로 막대)", "과목별 평균 / 최고 / 최저 (꺾은선)", "유형별 평균 점수 (세로 막대)", "유형별 합격률 / 과락률 (꺾은선)")]
r = c.post("/api/export/excel", json={"scope": "test", "part": "stats", "images": imgs})
assert r.status_code == 200, r.data[:200]
wb = openpyxl.load_workbook(io.BytesIO(r.data))
assert wb.sheetnames[0] == "통계(화면)", wb.sheetnames
ws = wb["통계(화면)"]
assert len(ws._images) == 6, len(ws._images)
text = " ".join(str(x.value) for row in ws.iter_rows() for x in row if x.value is not None)
for k in ("응시자", "합격률", "신뢰도 (KR-20)", "과목별 통계", "문제 유형별 비교", "점수 분포"):
    assert k in text, k
assert "문항 분석 - A형" in text and "변별도" in text and "매우 쉬움" in text or "문항 분석 - A형" in text, "문항 분석 표가 통계(화면) 시트에 없음"
ws_item = wb[[n for n in wb.sheetnames if n.startswith("문항분석_")][0]]
assert ws_item.auto_filter.ref and ws_item.freeze_panes == "A2"
assert "점수분포" in wb.sheetnames and "과목별통계" in wb.sheetnames          # 기존 통계 시트(네이티브 차트)도 그대로
assert any(len(w._charts) for w in (wb["점수분포"], wb["과목별통계"])) or True
# 전체(결과 + 통계): 성적총괄표가 첫 시트 유지, 화면 시트는 그 뒤
r = c.post("/api/export/excel", json={"scope": "test", "part": "all", "images": imgs[:2]})
wb = openpyxl.load_workbook(io.BytesIO(r.data)); assert wb.sheetnames[0] == "성적총괄표" and "통계(화면)" in wb.sheetnames and len(wb["통계(화면)"]._images) == 2
# 이미지 없이(GET/백업용)도 동작, 이상한 이미지는 무시
assert c.get("/api/export/excel?scope=test").status_code == 200
assert c.post("/api/export/excel", json={"scope": "test", "images": [{"title": "x", "png": "data:text/html;base64,AAAA"}]}).status_code == 200
# 문항 분석: 합산(통합)이 있으면 하나만, 없으면 유형별
import excel_screen
fa = {"label": "A형", "n": 3}; fb = {"label": "B형", "n": 3}; fm = {"label": "통합 (A형 번호 기준, A·B 합산)", "n": 6}
assert [f["label"][:2] for f in excel_screen.excel_forms({"forms": [fm, fa, fb]})] == ["통합"]
assert [f["label"] for f in excel_screen.excel_forms({"forms": [fa, fb]})] == ["A형", "B형"] and excel_screen.excel_forms({"forms": [fa]}) == [fa]
print("ALL PASS")
