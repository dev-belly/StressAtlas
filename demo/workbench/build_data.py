"""Build the offline browser snapshot from the repository's saved evidence."""

import argparse
import hashlib
import json
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parents[1]


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def source(label, path):
    normalized = (ROOT / path).read_text(encoding="utf-8").encode("utf-8")
    return {
        "label": label,
        "url": f"{REPOSITORY}/blob/main/{path}",
        "sha256_lf": hashlib.sha256(normalized).hexdigest(),
    }


def fact(label, value, kind=None):
    return {"label": label, "value": value, "format": kind}


def column(key, title, kind=None):
    return {"key": key, "title": title, "format": kind}


def order(key, title, descending=True):
    return {"key": key, "title": title, "id": key, "descending": descending}


def metric(label, value, note):
    return {"label": label, "value": value, "note": note}


def table(title, columns, rows, *formats):
    return {
        "title": title,
        "table": {"columns": columns, "rows": rows, "formats": formats},
    }


def theme(accent, muted, base, glow):
    return {
        "accent": accent,
        "accent-soft": muted,
        "hero-base": base,
        "hero-glow": glow,
    }


REPOSITORY = "https://github.com/dev-belly/StressAtlas"
INPUT = "demo/summary.json"


def build():
    result = read_json(INPUT)
    names = {
        "baseline": "基准",
        "recession": "衰退",
        "severe": "重度压力",
        "independent": "独立违约",
        "high_correlation": "高相关违约",
    }
    sector_names = {
        "manufacturing": "制造业",
        "real_estate": "房地产",
        "retail": "零售",
        "technology": "科技",
    }
    scenario_type = {
        "baseline": "参照情景",
        "recession": "经济压力",
        "severe": "经济压力",
        "independent": "相关性对照",
        "high_correlation": "相关性对照",
    }
    by_id = {row["scenario"]: row for row in result["scenarios"]}
    base = by_id[result["baseline_scenario"]]
    records = []
    for row in result["scenarios"]:
        identifier = row["scenario"]
        parts = sorted(
            (part for part in result["sector_es"] if part["scenario"] == identifier),
            key=lambda part: -part["es_contribution"],
        )
        records.append(
            {
                "id": identifier,
                "title": names[identifier],
                "subtitle": f"{scenario_type[identifier]} · {row['alpha']:.0%} 置信水平",
                "values": {
                    "type": scenario_type[identifier],
                    "es": row["es"],
                    "var": row["var"],
                    "mean": row["mean_loss"],
                    "delta": row["es"] - base["es"],
                },
                "facts": [
                    fact("风险暴露 EAD", row["stressed_ead"], "millionCny"),
                    fact("解析期望损失", row["analytic_el"], "millionCny"),
                    fact("模拟均值", row["mean_loss"], "millionCny"),
                    fact("均值模拟标准误", row["mean_mc_se"], "cny"),
                    fact("均值变动（vs 基准）", row["mean_delta"], "cny"),
                    fact("成对差分标准误", row["paired_mc_se"], "cny"),
                    fact("尾部有效路径数", row["tail_mass"], "integer"),
                ],
                "charts": {
                    "sector": [
                        {
                            "label": sector_names.get(part["sector"], part["sector"]),
                            "value": part["es_contribution"],
                        }
                        for part in parts
                    ]
                },
                "sections": [
                    table(
                        "ES 行业归因",
                        ["行业", "ES 贡献", "占比"],
                        [
                            [
                                sector_names.get(part["sector"], part["sector"]),
                                part["es_contribution"],
                                part["es_share"],
                            ]
                            for part in parts
                        ],
                        None,
                        "millionCny",
                        "percent",
                    )
                ],
            }
        )
    independent_es = by_id["independent"]["es"]
    if independent_es:
        ratio_es = f"{by_id['high_correlation']['es'] / independent_es:.2f}×"
        ratio_note = "高相关违约相对独立违约"
    else:
        ratio_es = "—"
        ratio_note = "独立违约情景的 ES 为 0，倍数无定义"
    confidence = f"{result['config']['alpha']:.0%}"
    return {
        "repository": REPOSITORY,
        "slug": "stressatlas",
        "project": "StressAtlas",
        "mark": "SA",
        "theme": theme("#a24c26", "#fff2eb", "#38241f", "#9a522e"),
        "workspace": "组合压力测试",
        "title": "尾部风险情景室",
        "description": "切换经济压力与违约相关性，比较组合损失、尾部风险与各行业的 ES 贡献。",
        "dataKind": "合成信贷组合 · 共同随机数模拟",
        "hero": metric(
            f"{confidence} ES 倍数", ratio_es, ratio_note
        ),
        "metrics": [
            metric(
                "配对模拟路径",
                f"{result['config']['paths']:,}",
                "各情景复用同一随机驱动",
            ),
            metric("基准 EAD", f"{result['base_ead'] / 1e6:.1f} 百万元", "人民币计价"),
            metric("情景", str(len(records)), f"覆盖 {result['sectors']} 个行业"),
        ],
        "recordHeading": "组合情景",
        "recordLabel": "情景",
        "detailHeading": "组合与行业证据",
        "searchPlaceholder": "搜索衰退、高相关等",
        "initialRecord": "high_correlation",
        "filters": [{"key": "type", "title": "情景类别"}],
        "columns": [
            column("es", "ES", "millionCny"),
            column("var", "VaR", "millionCny"),
            column("mean", "模拟均值", "millionCny"),
            column("delta", "ES 差值", "millionCny"),
        ],
        "orders": [order("es", "ES 最高"), order("mean", "均值损失最高")],
        "views": [
            {
                "id": "es",
                "label": "尾部 ES",
                "title": f"{confidence} ES 情景比较",
                "metric": "es",
                "format": "millionCny",
                "caption": "选择情景查看组合暴露、成对差分和行业贡献。",
            },
            {
                "id": "mean",
                "label": "平均损失",
                "title": "平均损失与相关性",
                "metric": "mean",
                "format": "millionCny",
                "caption": "比较独立与高相关情景在相同边际违约概率下的模拟均值。",
            },
            {
                "id": "sector",
                "label": "行业归因",
                "title": "所选情景的 ES 贡献",
                "mode": "detail",
                "format": "millionCny",
                "caption": "在组合尾部路径中分摊损失，行业贡献加总为组合 ES。",
            },
        ],
        "sourceNote": "工作台读取仓库保存的合成组合结果。VaR 为损失分位点，ES 为尾部平均损失。表中 ES 差值以基准情景为参照。",
        "sources": [
            source("保存的情景与归因", INPUT),
            source("原始模拟配置", "demo/inputs.json"),
            source("尾部取样与模型", "docs/METHODOLOGY.md"),
        ],
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify the generated snapshot"
    )
    args = parser.parse_args()
    document = build()
    records = document.pop("records")
    lines = ",\n".join(
        "    " + json.dumps(record, ensure_ascii=False, allow_nan=False)
        for record in records
    )
    header = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
    result = (
        "// Generated by build_data.py from repository evidence.\n"
        "window.workbenchData = "
        + header[:-2]
        + ',\n  "records": [\n'
        + lines
        + "\n  ]\n};\n"
    )
    path = DIRECTORY / "data.js"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != result:
            parser.exit(1, "Snapshot differs. Run build_data.py without --check.\n")
        print(f"Verified {len(records)} records")
    else:
        path.write_text(result, encoding="utf-8", newline="\n")
        print(f"Wrote {len(records)} records")


if __name__ == "__main__":
    main()
