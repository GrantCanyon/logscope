"""Автономное русское представление эксперимента точности и памяти Space-Saving."""

import json
import math
from pathlib import Path
import sys
from cli import Parser
from render_html import STYLE, number, table, text

COLORS = {"requests": "#63dbd3", "errors": "#ff827a"}
NAMES = {"requests": "Все запросы", "errors": "Только 5xx"}


def percent(value):
    return "—" if value is None else number(value * 100, 1) + "%"


def chart(dataset, kind, ordinal):
    rows = sorted(dataset["capacities"], key=lambda row: row["capacity"])
    width, height, left, top, plotw, ploth = 550, 270, 65, 30, 455, 180
    minimum, maximum = math.log2(rows[0]["capacity"]), math.log2(rows[-1]["capacity"])

    def x(capacity):
        return left + plotw * (
            (math.log2(capacity) - minimum) / (maximum - minimum)
            if maximum != minimum
            else 0.5
        )

    ceiling = (
        1
        if kind == "recall"
        else max(row["peak_rss_bytes"]["max"] for row in rows) / (1024**2) * 1.1
    )
    ceiling = ceiling or 1

    def y(value):
        return top + ploth * (1 - value / ceiling)

    title = (
        "Диапазон попадания в точный top-k"
        if kind == "recall"
        else "Пиковая память C-процесса, МиБ"
    )
    uid = f"{kind}-{ordinal}"
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="{uid}"><title id="{uid}">{title}</title>'
    ]
    for fraction in [0, 0.5, 1]:
        value = ceiling * fraction
        label = f"{fraction * 100:.0f}%" if kind == "recall" else f"{value:.1f}"
        parts.append(
            f'<line x1="{left}" x2="{left + plotw}" y1="{y(value):.2f}" y2="{y(value):.2f}" stroke="#263a50"/><text x="{left - 10}" y="{y(value) + 4:.2f}" text-anchor="end" fill="#a9bacd" font-size="12">{label}</text>'
        )
    for row in rows:
        parts.append(
            f'<text x="{x(row["capacity"]):.2f}" y="235" text-anchor="middle" fill="#a9bacd" font-size="12">{row["capacity"]}</text>'
        )
    parts.append(
        '<text x="300" y="262" text-anchor="middle" fill="#a9bacd" font-size="12">Ёмкость списка ключей, логарифмическая шкала</text>'
    )
    if kind == "recall":
        for name in ["requests", "errors"]:
            upper_points, lower_points = [], []
            offset = -3 if name == "requests" else 3
            for row in rows:
                recall = row["metrics"][name]["recall_at_k"]
                if recall["lower"] is None:
                    continue
                cx, low, high = (
                    x(row["capacity"]) + offset,
                    y(recall["lower"]),
                    y(recall["upper"]),
                )
                label = f"{NAMES[name]}: {percent(recall['lower'])}–{percent(recall['upper'])}"
                parts.append(
                    f'<line x1="{cx:.2f}" x2="{cx:.2f}" y1="{low:.2f}" y2="{high:.2f}" stroke="{COLORS[name]}" stroke-width="4" opacity=".7"><title>{text(label)}</title></line>'
                )
                for cy in [low, high]:
                    parts.append(
                        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="3" fill="{COLORS[name]}"/>'
                    )
                upper_points.append(f"{cx:.2f},{high:.2f}")
                lower_points.append(f"{cx:.2f},{low:.2f}")
            for points in [upper_points, lower_points]:
                if points:
                    parts.append(
                        f'<polyline points="{" ".join(points)}" fill="none" stroke="{COLORS[name]}" stroke-width="1" opacity=".6"/>'
                    )
    else:
        points = []
        for row in rows:
            memory = row["peak_rss_bytes"]
            cx, low, high = (
                x(row["capacity"]),
                y(memory["min"] / 1024**2),
                y(memory["max"] / 1024**2),
            )
            median = y(memory["median"] / 1024**2)
            parts.append(
                f'<line x1="{cx:.2f}" x2="{cx:.2f}" y1="{low:.2f}" y2="{high:.2f}" stroke="#f4bd74" stroke-width="4"/><circle cx="{cx:.2f}" cy="{median:.2f}" r="4" fill="#f4bd74"/>'
            )
            points.append(f"{cx:.2f},{median:.2f}")
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="#f4bd74" stroke-width="1.5"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def dataset_section(dataset, ordinal, top_k):
    rows = sorted(dataset["capacities"], key=lambda row: row["capacity"])
    sections = [
        f'<section><div class="eyebrow">Набор {ordinal + 1}</div><h2>{number(dataset["requested_cardinality"])} возможных товарных путей</h2><p class="muted">Фактически встречено: {number(dataset["truth"]["requests"]["distinct_count"])} ключей во всех запросах и {number(dataset["truth"]["errors"]["distinct_count"])} в 5xx. Ключ включает метод; отдельный /checkout также входит в D.</p>'
    ]
    sections.append(
        f'<div class="split"><div><h3>Попадание в top-{number(top_k)} при ничьих</h3><div class="legend"><span><i class="dot"></i>Все запросы</span><span><i class="dot red"></i>Только 5xx</span></div>{chart(dataset, "recall", ordinal)}</div><div><h3>Пиковая память C-процесса</h3><p class="muted">Точка — медиана, отрезок — min/max повторов.</p>{chart(dataset, "memory", ordinal)}</div></div>'
    )
    for name in ["requests", "errors"]:
        metric_rows = []
        for row in rows:
            metric = row["metrics"][name]
            recall, bounds = metric["recall_at_k"], metric["interval_width"]
            metric_rows.append(
                [
                    number(row["capacity"]),
                    number(metric["tracked_count"]),
                    percent(recall["lower"]),
                    percent(recall["upper"]),
                    number(bounds["mean"], 2),
                    number(bounds["max"]),
                    percent(metric["exact_tracked_fraction"]),
                ]
            )
        sections.append(
            f"<h3>{NAMES[name]} · N={number(dataset['truth'][name]['requests'])} · D={number(dataset['truth'][name]['distinct_count'])}</h3>"
        )
        sections.append(
            table(
                [
                    "Ёмкость",
                    "Отслеживается",
                    "Совпадение: нижняя",
                    "Совпадение: верхняя",
                    "Средняя ширина",
                    "Макс. ширина",
                    "Точные частоты среди отслеживаемых",
                ],
                metric_rows,
                tuple(range(7)),
            )
        )
    performance = [
        [
            number(row["capacity"]),
            number(row["median_seconds"] * 1000, 2),
            number(row["median_requests_per_second"], 0),
            f"{number(row['peak_rss_bytes']['min'] / 1024**2, 2)}–{number(row['peak_rss_bytes']['max'] / 1024**2, 2)}",
        ]
        for row in rows
    ]
    sections.append(
        "<h3>Время и память одного общего анализа</h3>"
        + table(
            ["Ёмкость", "Медиана, мс", "Запросов/с", "Пик памяти min–max, МиБ"],
            performance,
            (0, 1, 2, 3),
        )
    )
    sections.append(
        f"<details><summary>Воспроизводимость набора и порядок запусков</summary><p>Вход: {number(dataset['input_bytes'])} байт. SHA256: <code>{text(dataset['input_sha256'])}</code></p>"
    )
    for order in dataset["execution_order"]:
        label = "Прогрев" if order["warmup"] else f"Повтор {order['round']}"
        sections.append(
            f"<p>{text(label)}: <code>{text(' → '.join(str(c) for c in order['capacities']))}</code></p>"
        )
    sections.append("</details></section>")
    return "".join(sections)


def render_experiment(report):
    if not isinstance(report, dict) or report.get("experiment_schema_version") != 1:
        raise ValueError("Поддерживается только experiment_schema_version=1")
    params, environment, datasets = (
        report["parameters"],
        report["environment"],
        report["datasets"],
    )
    if not datasets:
        raise ValueError("В эксперименте нет наборов данных")
    checked = 0
    for dataset in datasets:
        if not dataset["capacities"]:
            raise ValueError("В наборе отсутствуют измерения")
        for row in dataset["capacities"]:
            if row["input_sha256"] != dataset["input_sha256"]:
                raise ValueError("Ёмкости получали разные входные файлы")
            for metric in row["metrics"].values():
                if metric["guarantees"]["violations"] != 0:
                    raise ValueError(
                        "Нельзя выдать успешный отчёт с нарушенной гарантией"
                    )
                checked += (
                    metric["guarantees"]["tracked_checked"]
                    + metric["guarantees"]["untracked_checked"]
                )
    contents = "".join(
        dataset_section(dataset, i, params["top_k"])
        for i, dataset in enumerate(datasets)
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>LogScope — точность и память Space-Saving</title><style>{STYLE}h3{{margin-top:24px}}details{{margin-top:20px}}.metrics{{grid-template-columns:repeat(3,minmax(0,1fr))}}.metrics .card strong{{white-space:nowrap;overflow-wrap:normal}}@media(max-width:600px){{.metrics{{grid-template-columns:1fr}}}}</style></head><body><main>
<header><div><div class="eyebrow">LogScope · воспроизводимый эксперимент</div><h1>Сколько стоит точность</h1><p class="muted">Один поток для всех ёмкостей. Точный Counter проверяет границы C-анализатора.</p></div><div class="badge">Space-Saving · JSON v1</div></header>
<div class="metrics"><div class="card"><span>Строк в наборе</span><strong>{number(params["requests"])}</strong><span>только синтетические данные</span></div><div class="card"><span>Измеренных точек</span><strong>{number(sum(len(d["capacities"]) for d in datasets))}</strong><span>{number(params["repeats"])} повторов после прогрева</span></div><div class="card"><span>Проверено ключей</span><strong>{number(checked)}</strong><span>отслеживаемые и пропущенные ключи; оба потока и все ёмкости</span></div></div>
<section><h2>Как читать результат</h2><p>Границы показателя совпадения — диапазон возможного совпадения с точным top-k при допустимом разрешении равных частот, <b>не доверительный интервал</b>. Верхняя граница оптимистическая. Широкий диапазон при одинаковых частотах сам по себе не означает ошибку оценки частот.</p><p>Ширина частотного интервала — upper − lower в числе запросов. Доля «Точных частот среди отслеживаемых» относится только к отслеживаемым ключам, а не ко всем ключам входа. При достаточной ёмкости все частоты должны быть точными.</p><p class="ok">Все проверенные интервалы и границы пропущенных ключей соблюдены. Суммы upper сверены с объёмами независимых потоков.</p></section>
{contents}
<section><h2>Методика и ограничения</h2><p>Для каждой кардинальности использован один файл с фиксированным seed={number(params["seed"])}. Фактическое число ключей D определено по файлу. Ёмкость меньше k не уменьшает знаменатель: k_effective=min(k,D). Для пустого потока показатель совпадения не определён и показан прочерк.</p><p>{text(params["timing"])}. Генерация, Counter, проверка метрик и HTML находятся вне измеряемого времени. {text(params["memory"])}. Память C включает базовую стоимость процесса и библиотек.</p><p>Порядок ёмкостей детерминированно перемешан по раундам. Время, пиковая память и совпадение с top-k не обязаны монотонно изменяться в каждом наборе. Кэш прогрет; холодный диск, сеть и параллельная нагрузка не измеряются. Результат одного окружения не является обещанием производительности.</p><p>ОС: {text(environment["os"])} {text(environment["os_release"])} · архитектура: {text(environment["architecture"])} · процессор: {text(environment["processor"])} · логических CPU: {number(environment["logical_cpus"])}.</p><p>Компилятор: <code>{text(environment["compiler"])}</code>. Python: {text(environment["python"])}.</p><p>SHA256 C-бинарника: <code>{text(environment["binary_sha256"])}</code>. Измерено: {text(report["measured_at_utc"])}.</p></section>
<footer>Автономный русский отчёт: без JavaScript и внешних ресурсов. Полные отсчёты и параметры сохранены в исходном JSON эксперимента.</footer>
</main></body></html>"""


def main():
    parser = Parser(
        description="Построить автономный русский HTML эксперимента точности и памяти."
    )
    parser.add_argument("input", type=Path, help="JSON эксперимента v1")
    parser.add_argument("--output", type=Path, required=True, help="Файл HTML")
    args = parser.parse_args()
    try:
        report = json.loads(args.input.read_text(encoding="utf-8-sig"))
        html = render_experiment(report)
        args.output.write_text(html, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError) as exc:
        print(f"Не удалось представить эксперимент: {exc}", file=sys.stderr)
        return 2
    print(f"Отчёт эксперимента создан: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
