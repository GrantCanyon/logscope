"""Автономный статический HTML из JSON v1: без JS, сети и потери точности uint64."""
from cli import Parser
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sys

STYLE = """
:root{color-scheme:dark;--bg:#09101a;--panel:#101e2e;--border:#263a50;--muted:#a9bacd;--text:#edf4fb;--cyan:#63dbd3;--red:#ff827a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 system-ui,sans-serif}main{max-width:1240px;margin:auto;padding:42px 28px 72px}header{display:flex;justify-content:space-between;gap:24px;align-items:start;border-bottom:1px solid var(--border);padding-bottom:30px}.eyebrow{font-size:12px;letter-spacing:.18em;color:var(--cyan);text-transform:uppercase}h1{font-size:42px;line-height:1.15;letter-spacing:-.035em;margin:12px 0}h2{font-size:22px;margin:0 0 18px}h3{font-size:16px;margin:0 0 12px}p{margin:10px 0}.muted,small{color:var(--muted)}.badge{white-space:nowrap;border:1px solid var(--border);border-radius:30px;padding:8px 14px;font-size:13px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:24px 0}.card,section{background:var(--panel);border:1px solid var(--border);border-radius:12px}.card{padding:20px}.card strong{display:block;font-size:28px;letter-spacing:-.03em;overflow-wrap:anywhere}.card span{font-size:13px;color:var(--muted)}section{padding:24px;margin:18px 0}.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}.split section{min-width:0;margin:0}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left;font-size:14px}th{color:var(--muted);font-weight:500}td,th{padding:12px 8px;border-bottom:1px solid var(--border);vertical-align:top}tr:last-child td{border-bottom:0}td.num,th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}code{font:13px ui-monospace,monospace;overflow-wrap:anywhere}.signal{color:var(--red)}.ok{color:var(--cyan)}svg{display:block;width:100%;height:auto}.legend{display:flex;gap:20px;font-size:13px;color:var(--muted)}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--cyan);margin-right:6px}.dot.red{background:var(--red)}details summary{cursor:pointer;color:var(--cyan)}footer{margin-top:32px;font-size:13px;color:var(--muted)}.empty{padding:18px 0;color:var(--muted)}
@media(max-width:800px){main{padding:24px 16px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.split{grid-template-columns:1fr}header{display:block}.badge{display:inline-block;margin-top:12px}h1{font-size:34px}.card strong{font-size:24px}section{padding:18px}}
@media print{:root{--bg:white;--panel:white;--text:#172b3e;--muted:#43566a;--border:#c9d1d9;--cyan:#137d78;--red:#b52f2f;color-scheme:light}main{max-width:none;padding:0}section,.card{break-inside:avoid}details{display:block}}
"""

def text(value):
    return escape(str(value), quote=True)

def number(value, digits=0):
    if value is None:
        return "—"
    return f"{value:,.{digits}f}".replace(",", " ") if isinstance(value, float) else f"{value:,}".replace(",", " ")

def utc(ms):
    if ms is None:
        return "нет событий"
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OverflowError, OSError, ValueError):
        return f"epoch_ms {ms} (вне диапазона календаря)"

def table(headers, rows, numeric=()):
    th = "".join(f'<th scope="col" class="{"num" if i in numeric else ""}">{text(h)}</th>' for i,h in enumerate(headers))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{cell}</td>' for i,cell in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>' if body else '<p class="empty">Данных нет.</p>'

def chart(buckets, alerts):
    width, height, left, top, plotw, ploth = 1120, 270, 64, 20, 1016, 190
    maximum = max((b["requests"] for b in buckets), default=1) or 1
    first = buckets[0]["minute"] if buckets else 0
    last = buckets[-1]["minute"] if buckets else 0
    span = max(last-first+1, 1)
    alert_minutes = {a["minute"] for a in alerts}
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="chart-title chart-desc"><title id="chart-title">Запросы и доля 5xx по минутам</title><desc id="chart-desc">Столбцы показывают число запросов, красная линия — процент 5xx. Точные значения доступны в таблице ниже.</desc>']
    for fraction in [0, .5, 1]:
        y = top + ploth * (1-fraction)
        parts.append(f'<line x1="{left}" y1="{y}" x2="{left+plotw}" y2="{y}" stroke="#263a50"/><text x="{left-10}" y="{y+4}" text-anchor="end" fill="#a9bacd" font-size="12">{number(round(maximum*fraction))}</text><text x="{left+plotw+8}" y="{y+4}" fill="#a9bacd" font-size="12">{int(fraction*100)}%</text>')
    points=[]
    for b in buckets:
        x = left + (b["minute"]-first+.5)/span*plotw
        y = top + ploth*(1-b["requests"]/maximum)
        barw = max(.2, min(24, plotw/span*.7))
        fill = "#f4bd74" if b["minute"] in alert_minutes else "#63dbd3"
        label = f'{utc(b["minute"]*60000)} · запросов {b["requests"]} · 5xx {b["error_rate_pct"]:.2f}%'
        parts.append(f'<rect x="{x-barw/2:.2f}" y="{y:.2f}" width="{barw:.2f}" height="{top+ploth-y:.2f}" fill="{fill}" opacity=".65"><title>{text(label)}</title></rect>')
        points.append(f'{x:.2f},{top+ploth*(1-b["error_rate_pct"]/100):.2f}')
    if points:
        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="#ff827a" stroke-width="2"/>')
    if buckets:
        for anchor, x, m in [('start',left,first),('end',left+plotw,last)]:
            parts.append(f'<text x="{x}" y="245" text-anchor="{anchor}" fill="#a9bacd" font-size="12">{text(utc(m*60000))}</text>')
    parts.append('</svg>')
    return ''.join(parts)

def endpoints(entries):
    return table(["Метод и конечная точка", "Нижняя", "Верхняя"], [
        [f'<code>{text(e["method"])} {text(e["target"])}</code>',number(e["lower"]),number(e["upper"])] for e in entries], (1,2))

def validate(report):
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise ValueError("Поддерживается только schema_version=1")
    # JSON встраивается только через экранированный текст. Проверка структур нужна
    # для понятной диагностики, а не как замена экранированию.
    for name in ["incident"] + (["baseline"] if report.get("baseline") is not None else []):
        a = report[name]
        for key in ["summary", "input", "window"]:
            if not isinstance(a[key], dict):
                raise ValueError("Некорректная структура отчёта")
        for key in ["requests", "errors_5xx", "bytes"]:
            n = a["summary"][key]
            if type(n) is not int or n < 0 or n > 2**64-1:
                raise ValueError("Счётчик должен быть целым uint64")
    return report

def render(report):
    r = validate(report)
    a, settings = r["incident"], r["settings"]
    s, inp, w = a["summary"], a["input"], a["window"]
    lat, alerts = s["latency_us"], r["alerts"]
    cards = [("Запросы",number(s["requests"]),"принятые после фильтров"), ("Ошибки 5xx",number(s["errors_5xx"]),f'{number(s["error_rate_pct"],2)}% запросов'), ("Средняя задержка",number(lat["mean"],1),"микросекунды"), ("Сигналы",number(len(alerts)),"минут с повышенной долей 5xx")]
    card_html = ''.join(f'<div class="card"><span>{title}</span><strong>{value}</strong><span>{hint}</span></div>' for title,value,hint in cards)
    alert_rows = [[text(utc(x["minute"]*60000)),number(x["requests"]),number(x["errors_5xx"]),number(x["error_rate_pct"],2)+"%"] for x in alerts]
    quantiles=[]
    for p in ["p50","p95","p99"]:
        q=lat[p]
        quantiles.append([p, "—" if q is None else f'[{number(q["lower"])}, {number(q["upper"])}] мкс'])
    comparison = '<p class="muted">Базовый период не задан.</p>'
    if r.get("comparison"):
        c=r["comparison"]
        comparison=table(["Метрика", "Изменение"], [
            ["Отношение количества запросов",number(c["request_ratio"],3)],
            ["Изменение доли 5xx, п.п.",number(c["error_rate_delta_pp"],3)],
            ["Отношение средней задержки",number(c["mean_latency_ratio"],3)]],(1,))
        comparison += '<p class="muted">Отношение количества запросов не является сравнением пропускной способности: длительности периодов могут различаться.</p>'
    status_table=table(["Код HTTP","Количество"],[[text(k),number(v)] for k,v in s["status_codes"].items()],(1,))
    bucket_table=table(["Минута UTC","Запросы","5xx","Доля 5xx"],[[text(utc(b["minute"]*60000)),number(b["requests"]),number(b["errors_5xx"]),number(b["error_rate_pct"],2)+"%"] for b in w["buckets"]],(1,2,3))
    reasons={"columns":"число полей","number":"целое число","method":"метод","target":"конечная точка","status":"код HTTP","latency":"задержка","line_too_long":"слишком длинная строка","binary":"нулевой байт"}
    rejection=table(["Причина","Количество"],[[text(reasons.get(k,k)),number(v)] for k,v in inp["reasons"].items()],(1,)) if inp["rejected"] else '<p class="ok">Отклонённых строк нет.</p>'
    return f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>LogScope — отчёт по инциденту</title><style>{STYLE}</style></head><body><main>
<header><div><div class="eyebrow">LogScope · анализ журналов</div><h1>Картина инцидента</h1><p class="muted">{text(utc(s["first_ms"]))} — {text(utc(s["last_ms"]))}</p></div><div class="badge">Автономный отчёт · JSON v1</div></header>
<div class="metrics">{card_html}</div>
<section><h2>Как менялось поведение</h2><div class="legend"><span><i class="dot"></i>Запросы</span><span><i class="dot red"></i>Доля 5xx</span><span>Янтарные столбцы — сигналы</span></div>{chart(w["buckets"],alerts)}<p class="muted">Последние {number(w["capacity_minutes"])} минут по времени событий; пустые минуты пропущены. Вне окна: {number(w["outside_window"])} запросов. Вытеснено: {number(w["evicted_requests"])}.</p><details><summary>Точные значения по минутам</summary>{bucket_table}</details></section>
<section><h2>Минуты, требующие внимания</h2>{table(["Начало минуты UTC","Запросы","5xx","Доля 5xx"],alert_rows,(1,2,3))}<p class="muted">Правило: не менее {number(settings["alert_min_requests"])} запросов и доля 5xx ≥ {number(settings["alert_rate_pct"],2)}%. При непустом базовом периоде дополнительно: доля ≥ 3 × базовая доля и ≥ базовая доля + 5 п.п. Это объяснимая эвристика, не статистический тест и не доказательство причины.</p></section>
<div class="split"><section><h2>Где больше запросов</h2>{endpoints(a["top_requests"])}</section><section><h2>Где больше ошибок 5xx</h2>{endpoints(a["top_errors"])}</section></div>
<p class="muted">Каждый ключ — метод + конечная точка. Границы относятся к количеству запросов. При вытеснениях ранжирование приближённое; непоказанный ключ тоже может быть значимым. Ёмкость каждого списка: {number(settings["capacity"])}.</p>
<div class="split"><section><h2>Сравнение с базовым периодом</h2>{comparison}</section><section><h2>Задержки</h2>{table(["Квантиль","Содержащий интервал"],quantiles)}<p class="muted">Интервалы степеней двойки, а не точные значения квантилей. Минимум: {number(lat["min"])} мкс · максимум: {number(lat["max"])} мкс.</p></section></div>
<section><h2>Качество входных данных</h2><p>Строк: <b>{number(inp["lines"])}</b> · корректных: <b>{number(inp["valid"])}</b> · отклонено: <b>{number(inp["rejected"])}</b> · отфильтровано: <b>{number(inp["filtered"])}</b>.</p>{rejection}<p class="muted">Объём ответов принятой выборки: {number(s["bytes"])} байт. Содержимое отклонённых строк не сохраняется.</p><details><summary>Распределение кодов HTTP</summary>{status_table}</details></section>
<footer>LogScope {text(r.get("tool_version",""))} · Без внешних ресурсов и JavaScript. Отчёт содержит ключи принятых конечных точек: проверьте их перед передачей третьим лицам. Все временные отметки — UTC.</footer>
</main></body></html>'''

def main():
    parser=Parser(description="Преобразовать отчёт LogScope JSON v1 в автономный HTML.")
    parser.add_argument("input",type=Path,help="Файл JSON")
    parser.add_argument("--output",type=Path,required=True,help="Файл HTML")
    args=parser.parse_args()
    try:
        source=json.loads(args.input.read_text(encoding="utf-8-sig"),parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Недопустимое число JSON")))
        page=render(source)
        args.output.write_text(page,encoding="utf-8")
    except (OSError,ValueError,KeyError,TypeError,IndexError,OverflowError) as exc:
        print(f"Не удалось сформировать HTML: {exc}",file=sys.stderr)
        return 2
    print(f"Создано: {args.output}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
