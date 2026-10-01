# StressAtlas｜信用组合压力测试

**单个借款人的违约概率不变，整个贷款组合的极端损失为什么还会变大？**

如果借款人的违约更加集中，平均损失可能基本不变，但最坏情形的损失会变大。StressAtlas 用全局、行业和借款人自身三个随机驱动模拟违约，比较压力情景并把尾部风险分配到行业。

![保存结果的可复现图表](docs/evidence.png)

## 快速运行

```bash
python -m pip install -r requirements-replay.txt
python -m pip install -e .
stressatlas demo --out outputs
stressatlas verify --out outputs
python -m unittest discover -s tests -v
```

打开 `outputs/report.html` 查看离线报告。需要 Python 3.11+、NumPy 和 SciPy。

## 已实现的重点

- 同一借款人的多笔贷款共享一次违约，拆分贷款不会制造虚假分散效果。
- 所有情景复用同一组随机样本，输出逐路径配对差异及均值的蒙特卡洛标准误。
- 同时给出解析期望损失、模拟平均损失、99% VaR 和 ES。
- 离散 ES 按固定尾部质量计算，正确处理分数边界和并列损失。
- 行业使用同一组组合尾部权重，贡献之和等于组合 ES。
- 报告能从保存的输入、参数和随机种子完整重放；修改指标再更新哈希仍会被发现。

合成演示：160 笔贷款、80 个借款人、4 个行业、基础敞口 1.696 亿元、20,000 次模拟。保持边际风险不变时，独立违约、基础相关性、较高相关性情景的解析期望损失都约为 336.63 万元；99% ES 分别约为 962.15 万、1,847.62 万、2,770.63 万元。

[数学方法](docs/METHODOLOGY.md) · [字段合同](docs/DATA_CONTRACT.md) · [完整案例](docs/CASE_STUDY.md) · [中文面试问答与简历表述](docs/INTERVIEW.md)

PD、LGD、EAD 均为合成假设。模型是一周期研究原型，没有真实银行组合、宏观参数校准或监管资本计算的声明。
