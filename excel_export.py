"""엑셀 내보내기: 결과/문항별 답안 + 통계 시트 + 엑셀 차트(세로 막대, 꺾은선). 시험 설정(과목 구성 등)에 맞춰 열을 만든다."""
import io

import pandas as pd
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.worksheet.properties import PageSetupProperties

import exam as exam_mod

FLAG_TEXT = {"key": "정답키 확인", "hard": "매우 어려움", "easy": "매우 쉬움", "lowdisc": "변별도 낮음",
             "revdisc": "역변별", "marking": "마킹 이상"}
BLUE, GREEN, RED, ORANGE, PURPLE = "2563EB", "059669", "DC2626", "EA580C", "7C3AED"


def _value_labels(ch):
    """데이터 레이블은 '값'만 (구간 이름/계열 이름/범례 표시는 끔)."""
    ch.dataLabels = DataLabelList()
    ch.dataLabels.showVal = True
    ch.dataLabels.showCatName = False
    ch.dataLabels.showSerName = False
    ch.dataLabels.showLegendKey = False
    ch.dataLabels.showPercent = False


def _fit_page(ws):
    """차트가 있는 시트: 가로 방향, 한 페이지에 맞춰 인쇄."""
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 1


def _style_axes(ch, x_title, y_title, y_max=None, y_min=0):
    ch.x_axis.title, ch.y_axis.title = x_title, y_title
    ch.x_axis.delete = False          # openpyxl 기본값이 축을 숨기는 뷰어가 있어 명시
    ch.y_axis.delete = False
    ch.y_axis.scaling.min = y_min
    if y_max is not None:
        ch.y_axis.scaling.max = y_max
    ch.height, ch.width = 8.5, 17


def _color(series, hex_color, dash=None, line=False, width=28575):
    if line:
        series.graphicalProperties.line.solidFill = hex_color
        series.graphicalProperties.line.width = width
        if dash:
            series.graphicalProperties.line.dashStyle = dash
        series.marker.symbol = "none" if dash else "circle"
        series.smooth = False
    else:
        series.graphicalProperties.solidFill = hex_color
        series.graphicalProperties.line.solidFill = hex_color


def _bar(ws, title, cat_col, val_cols, n_rows, anchor, x_title, y_title, colors, y_max=None, labels=True):
    ch = BarChart()
    ch.type, ch.title, ch.grouping = "col", title, "clustered"
    for k, col in enumerate(val_cols):
        ch.add_data(Reference(ws, min_col=col, min_row=1, max_row=n_rows + 1), titles_from_data=True)
        _color(ch.series[k], colors[k])
    ch.set_categories(Reference(ws, min_col=cat_col, min_row=2, max_row=n_rows + 1))
    if labels:
        _value_labels(ch)
    _style_axes(ch, x_title, y_title, y_max)
    ch.legend = None if len(val_cols) == 1 else ch.legend
    ws.add_chart(ch, anchor)
    return ch


def _line(ws, title, cat_col, val_cols, n_rows, anchor, x_title, y_title, colors, y_max=None, dashes=None, labels=True):
    ch = LineChart()
    ch.title = title
    for k, col in enumerate(val_cols):
        ch.add_data(Reference(ws, min_col=col, min_row=1, max_row=n_rows + 1), titles_from_data=True)
        _color(ch.series[k], colors[k], dash=(dashes or {}).get(k), line=True)
    ch.set_categories(Reference(ws, min_col=cat_col, min_row=2, max_row=n_rows + 1))
    if labels:
        _value_labels(ch)
    _style_axes(ch, x_title, y_title, y_max)
    for k, ser in enumerate(ch.series):      # 점선 기준선(합격 평균/과락 점수)은 값 레이블을 끈다
        if (dashes or {}).get(k) in ("sysDot",):
            ser.dLbls = DataLabelList()
            ser.dLbls.showVal = ser.dLbls.showSerName = ser.dLbls.showCatName = ser.dLbls.showLegendKey = False
    ws.add_chart(ch, anchor)
    return ch


def _bar_with_refs(ws, title, cat_col, bar_col, ref_cols, n_rows, anchor, x_title, y_title, y_max, ref_colors):
    """막대 + 기준선(합격 평균/과락 점수)을 한 차트에."""
    bar = _bar(ws, title, cat_col, [bar_col], n_rows, anchor, x_title, y_title, [BLUE], y_max)
    ws._charts.remove(bar)           # 겹쳐 그린 뒤 다시 추가
    ln = LineChart()
    for k, col in enumerate(ref_cols):
        ln.add_data(Reference(ws, min_col=col, min_row=1, max_row=n_rows + 1), titles_from_data=True)
        _color(ln.series[k], ref_colors[k], dash="dash", line=True)
    bar.legend = ln.legend
    bar += ln
    ws.add_chart(bar, anchor)


def build(exam, spq, judged, st, st_scope, pass_average, subject_fail_cutoff, part="all"):
    """judged: with_judge 결과 목록, st: stats.compute 결과. part='stats' 이면 통계 시트만."""
    subj = exam_mod.subject_ranges(exam)
    n_q = exam_mod.total_questions(exam)
    tmax = n_q * spq
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as w:
        if part != "stats":
            totals = [x["total_score"] for x in judged]
            rows, detail = [], []
            for it in judged:
                ss = it["subject_scores"]
                r = {"구분": "테스트" if it.get("is_test") else "", "순번": it["id"] + 1, "파일명": it["filename"],
                     "문제유형": it["exam_type"], "수험번호": it["student_id"]}
                for code, name, short, a, b in subj:
                    r[f"{code}.{short}({(b - a + 1) * spq:g})"] = ss[code]
                r.update({f"총점({tmax:g})": it["total_score"], "평균": it["average"],
                          "석차": 1 + sum(1 for t in totals if t > it["total_score"]), "판정": it["result"],
                          "과락과목": ",".join(it["fail_subjects"]), "무표기": it["blank_count"],
                          "이중마킹": it["multi_count"], "검수필요여부": "검수필요" if it["needs_review"] else "정상"})
                rows.append(r)
                sa = it.get("student_answers", {})
                d = {"수험번호": it["student_id"], "유형": it["exam_type"]}
                for q in range(1, n_q + 1):
                    d[str(q)] = sa.get(str(q), "")
                detail.append(d)
            pd.DataFrame(rows).to_excel(w, sheet_name="성적총괄표", index=False)
            pd.DataFrame(detail).to_excel(w, sheet_name="문항별답안", index=False)

        sm = st["summary"]
        pd.DataFrame([
            ("시험", exam["name"]), ("통계 대상", "실제 응시자" if st_scope == "real" else "테스트 결과"),
            ("응시자 수", sm["n"]), ("합격", sm["pass"]), ("불합격", sm["fail"]), ("과락", sm["cut"]),
            ("합격률(%)", sm["pass_rate"]), ("과락률(%)", sm["cut_rate"]),
            (f"총점 평균({tmax:g}점 만점)", sm["total_mean"]), ("총점 중앙값", sm["total_median"]), ("총점 표준편차", sm["total_sd"]),
            ("총점 최고", sm["total_max"]), ("총점 최저", sm["total_min"]),
            ("평균점수 평균", sm["avg_mean"]), ("평균점수 표준편차", sm["avg_sd"]),
            ("합격 평균(기준)", pass_average), ("과락 점수(기준)", subject_fail_cutoff),
            ("검수필요 건수", sm["review"]), ("무표기 문항 합계", sm["blank_total"]), ("이중마킹 문항 합계", sm["multi_total"]),
        ], columns=["항목", "값"]).to_excel(w, sheet_name="통계요약", index=False)

        # --- 점수 분포 + 차트 2개 (세로 막대 / 꺾은선)
        dist = pd.DataFrame([{"평균점수 구간": d["label"], "인원": d["count"]} for d in st["distribution"]])
        dist.to_excel(w, sheet_name="점수분포", index=False)
        ws = w.sheets["점수분포"]
        ws.column_dimensions["A"].width = 16
        top = max(d["count"] for d in st["distribution"]) + 1
        _bar(ws, "점수 분포 (세로 막대)", 1, [2], 10, "D2", "평균점수 구간", "인원(명)", [BLUE])
        _line(ws, "점수 분포 (꺾은선)", 1, [2], 10, "D20", "평균점수 구간", "인원(명)", [GREEN])
        _fit_page(ws)

        # --- 과목별 + 차트 2개
        sj = pd.DataFrame([{"과목": f'{s["code"]}.{s["name"]}', "약칭": f'{s["code"]}.{s["short"]}',
                            f'평균({s["max_score"]:g}점 만점)': s["mean"], "표준편차": s["sd"], "최고": s["max"], "최저": s["min"],
                            "정답률(%)": s["correct_rate"], "난이도": s["level"], "과락 인원": s["cut_n"],
                            "과락률(%)": s["cut_rate"], "합격 평균": pass_average, "과락 점수": subject_fail_cutoff}
                           for s in st["subjects"]])
        sj.to_excel(w, sheet_name="과목별통계", index=False)
        ws = w.sheets["과목별통계"]
        ws.column_dimensions["A"].width = 34
        n = len(sj)
        mean_c, max_c, min_c, pa_c, fc_c = 3, 5, 6, 11, 12
        ymax = max(st["subjects"][0]["max_score"], 1)
        _bar_with_refs(ws, "과목별 평균 점수 (세로 막대)", 2, mean_c, [pa_c, fc_c], n, "A" + str(n + 4), "과목", "점수", ymax, [RED, ORANGE])
        _line(ws, "과목별 평균 / 최고 / 최저 (꺾은선)", 2, [max_c, mean_c, min_c, pa_c, fc_c], n, "L" + str(n + 4),
              "과목", "점수", [PURPLE, GREEN, ORANGE, RED, "F59E0B"], ymax, dashes={0: "dash", 2: "dash", 3: "sysDot", 4: "sysDot"})
        _fit_page(ws)

        # --- 유형별 + 차트 2개
        fm = pd.DataFrame([{"유형": f["form"], "유형(응시자)": f'{f["form"]}형 ({f["n"]}명)', "응시자": f["n"],
                            f"총점 평균({tmax:g})": f["total_mean"], "평균점수 평균": f["avg_mean"], "합격률(%)": f["pass_rate"],
                            "과락 인원": f["cut_n"], "과락률(%)": round(f["cut_n"] / f["n"] * 100, 1), "합격 평균": pass_average}
                           for f in st["forms_summary"]])
        fm.to_excel(w, sheet_name="유형별통계", index=False)
        ws = w.sheets["유형별통계"]
        n = len(fm)
        _bar_with_refs(ws, "유형별 평균점수 (세로 막대)", 2, 5, [9], n, "A" + str(n + 4), "문제 유형", "평균점수", st["avg_full"], [RED])
        _line(ws, "유형별 합격률 / 과락률 (꺾은선)", 2, [6, 8], n, "L" + str(n + 4), "문제 유형", "비율(%)", [GREEN, RED], 100)
        _fit_page(ws)

        if st.get("blocks"):
            pd.DataFrame([{"과목": b["subject_name"], "첫 유형 번호(과목 내)": b["block"],
                           f'{b["form_a"]}형 응시자 정답률(%)': b["a_rate"], f'{b["form_b"]}형 응시자 정답률(%)': b["b_rate"],
                           "차이(B-A, %p)": b["diff"], f'{b["form_a"]}형 응시자 수': b["n_a"], f'{b["form_b"]}형 응시자 수': b["n_b"]}
                          for b in st["blocks"]]).to_excel(w, sheet_name="블록정답률", index=False)

        used = set()
        chs = list(range(1, exam["choices"] + 1))
        for fm_ in st["forms"]:
            row = []
            for it in fm_["items"]:
                r = {"문항": it["q"], "대응 번호": it["alt_q"], "과목": it["subject_name"], "정답": it["answer"],
                     "정답률(%)": it["correct_rate"], "오답률(%)": it["wrong_rate"]}
                for c in chs:
                    r[f"{c}번(%)"] = it["choices"][str(c)]
                r.update({"무표기(%)": it["choices"]["무표기"], "이중(%)": it["choices"]["이중"], "최다 오답": it["top_wrong"],
                          "최다 오답(%)": it["top_wrong_rate"], "변별도": it["disc"],
                          "검토": ", ".join(FLAG_TEXT[f] for f in it["flags"])})
                row.append(r)
            base = "문항분석_" + fm_["label"].split(" ")[0]
            name, k = base, 2
            while name in used:
                name, k = f"{base}{k}", k + 1
            used.add(name)
            pd.DataFrame(row).to_excel(w, sheet_name=name[:31], index=False)
    out.seek(0)
    return out
