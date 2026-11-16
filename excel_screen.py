"""엑셀의 '통계(화면)' 시트: 결과 확인/통계 화면과 같은 모양(요약 카드, 화면에 보이는 그래프 그림, 표)으로 만든다."""
import base64
import io
import time

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L

NC = 18                    # 시트 열 개수 (열 너비 9.6 x 18)
FONT = "맑은 고딕"
LEVEL_FILL = {"쉬움": "DCFCE7", "보통": "FEF3C7", "어려움": "FEE2E2"}


def add_screen_sheet(wb, exam, st, st_scope, tmax, pass_average, cut, images, first=False):
    ws = wb.create_sheet("통계(화면)")
    pos = 0 if first else min(2, len(wb.sheetnames) - 1)
    wb._sheets.remove(ws)
    wb._sheets.insert(pos, ws)
    for i in range(1, NC + 1):
        ws.column_dimensions[L(i)].width = 9.6
    ws.sheet_view.showGridLines = False
    thin = Side(style="thin", color="CBD5E1")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    sm = st["summary"]

    def put(r, c, v, span=1, bold=False, size=10, color="1E293B", fill=None, align="center", border=True, height=None):
        cell = ws.cell(row=r, column=c, value=v)
        cell.font = Font(name=FONT, bold=bold, size=size, color=color)
        cell.alignment = Alignment(horizontal=align, vertical="center")
        if span > 1:
            ws.merge_cells(start_row=r, start_column=c, end_row=r, end_column=c + span - 1)
        for k in range(c, c + span):
            x = ws.cell(row=r, column=k)
            if border:
                x.border = box
            if fill:
                x.fill = PatternFill("solid", fgColor=fill)
        if height:
            ws.row_dimensions[r].height = height
        return cell

    put(1, 1, f'{exam.get("title") or exam["name"]} - 결과 통계', NC, True, 16, "1E3A8A", align="left", border=False, height=28)
    put(2, 1, f'{"실제 응시자" if st_scope == "real" else "테스트 결과"} {sm["n"]}명 | 과락: 한 과목이라도 {cut:g}점 미만 | 합격: 과락 없이 평균 {pass_average:g}점 이상 '
              f'(총점 만점 {tmax:g}) | 출력 {time.strftime("%Y-%m-%d %H:%M")}', NC, False, 9, "64748B", align="left", border=False)
    kr = " / ".join(f'{f["label"].split(" ")[0]} {f["kr20"] if f["kr20"] is not None else "-"}' for f in st["forms"])
    cards = [("응시자", f'{sm["n"]}명', f'검수필요 {sm["review"]}건'),
             ("합격률", f'{sm["pass_rate"]}%', f'합격 {sm["pass"]} · 불합격 {sm["fail"]} · 과락 {sm["cut"]}'),
             ("과락률", f'{sm["cut_rate"]}%', f'{sm["cut"]}명'),
             ("평균점수 평균", sm["avg_mean"], f'중앙값 {sm["avg_median"]} · 표준편차 {sm["avg_sd"]}'),
             ("총점 평균", sm["total_mean"], f'{tmax:g}점 만점 · 최고 {sm["total_max"]} / 최저 {sm["total_min"]}'),
             ("신뢰도 (KR-20)", kr or "-", "1에 가까울수록 일관된 시험")]
    for k, (lab, val, sub) in enumerate(cards):
        r0, c0 = 4 + (k // 3) * 4, 1 + (k % 3) * 6
        put(r0, c0, lab, 6, False, 9, "64748B", "F8FAFC", "left")
        put(r0 + 1, c0, val, 6, True, 18, "1E3A8A", "F8FAFC", "left", height=30)
        put(r0 + 2, c0, sub, 6, False, 9, "64748B", "F8FAFC", "left")
    r = 4 + 2 * 4 + 1

    pics = []
    try:
        from openpyxl.drawing.image import Image as XImage       # Pillow 가 있어야 그림을 넣을 수 있다
        for im in images:
            raw = base64.b64decode(im["png"].split(",", 1)[-1])
            pics.append((str(im.get("title", "")), XImage(io.BytesIO(raw))))
    except Exception:
        pics = []
        if images:
            put(r, 1, "※ 화면 그래프 그림을 넣으려면 서버에서 'pip install pillow'가 필요합니다. 그래프는 점수분포/과목별통계/유형별통계 시트의 차트를 사용하세요.",
                NC, False, 9, "B45309", align="left", border=False)
            r += 2

    def pair(items, r):
        for i in range(0, len(items), 2):
            for j, (t, img) in enumerate(items[i:i + 2]):
                c = 1 + j * 9
                put(r, c, t, 8, True, 11, "1E293B", align="left", border=False)
                img.width, img.height = 560, 300
                ws.add_image(img, f"{L(c)}{r + 1}")
            r += 17
        return r

    def section(title, r):
        put(r, 1, title, NC, True, 12, "1E3A8A", align="left", border=False, height=22)
        return r + 1

    def table(header, spans, rows, r, fills=None):
        c = 1
        for h, sp in zip(header, spans):
            put(r, c, h, sp, True, 10, "334155", "F1F5F9")
            c += sp
        r += 1
        for row in rows:
            c = 1
            for k, (v, sp) in enumerate(zip(row, spans)):
                put(r, c, v, sp, False, 10, "1E293B", fills(k, v) if fills else None, "left" if k == 0 else "center")
                c += sp
            r += 1
        return r + 1

    take = lambda kw: [p for p in pics if kw in p[0]]
    r = section("점수 분포", r)
    r = pair(take("점수 분포"), r)
    r = section("과목별 통계", r)
    r = table(["과목", "평균 점수 (만점)", "표준편차", "최고", "최저", "정답률", "난이도", "과락 인원", "과락률"], [4, 2, 2, 1, 1, 2, 2, 2, 2],
              [(f'{x["code"]}. {x["name"]}', f'{x["mean"]} ({x["max_score"]:g})', x["sd"], x["max"], x["min"], f'{x["correct_rate"]}%',
                x["level"], f'{x["cut_n"]}명', f'{x["cut_rate"]}%') for x in st["subjects"]], r,
              lambda k, v: LEVEL_FILL.get(v) if k == 6 else None)
    r = pair(take("과목별"), r)
    r = section("문제 유형별 비교", r)
    r = table(["유형", "응시자", "총점 평균", "평균점수 평균", "합격률", "과락 인원"], [3, 3, 3, 3, 3, 3],
              [(f'{f["form"]}형', f'{f["n"]}명', f["total_mean"], f["avg_mean"], f'{f["pass_rate"]}%', f'{f["cut_n"]}명')
               for f in st["forms_summary"]], r)
    r = pair(take("유형별"), r)
    if st.get("blocks"):
        b0 = st["blocks"][0]
        r = section(f'블록 정답률 비교 (같은 문제를 앞/뒤에서 풀었을 때, {b0["form_a"]}형 번호 기준)', r)
        r = table(["과목", "번호", f'{b0["form_a"]}형 응시자 정답률', f'{b0["form_b"]}형 응시자 정답률', "차이 (%p)"], [5, 3, 3, 3, 4],
                  [(b["subject_name"], b["block"], f'{b["a_rate"]}%', f'{b["b_rate"]}%', b["diff"]) for b in st["blocks"]], r)
    put(r, 1, "문항 분석(문항별 정답률, 선택지 비율, 변별도, 정답키 확인)은 '문항분석_*' 시트에 있습니다. 변별도: 상위 27% 정답률 - 하위 27% 정답률 (0.3 이상 좋음, 0.2 미만 '변별도 낮음', 음수는 '역변별' = 점수 낮은 응시자가 더 많이 맞힌 문항). KR-20: 0.8 이상 양호.",
        NC, False, 9, "64748B", align="left", border=False)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    wb.active = pos
