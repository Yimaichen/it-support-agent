# 评估入口

应用只有 Pi 执行路径，当前契约见 [SPEC](../SPEC.md)。评估分为离线定义检查、检索检查、真实模型对话和人工证据审阅；脚本运行完成不等于业务语义通过。

## 离线检查

```bash
uv run python -m evals.contracts
uv run python -m evals.retrieval --split tuning --output evals/runs/local-tuning.json
uv run python -m evals.retrieval --split holdout --output evals/runs/local-holdout.json
uv run pytest tests/test_retrieval_eval.py
```

检索使用固定合成资料和临时数据库，不读取运行聊天，也不调用模型。输出文件已存在时拒绝覆盖。指标定义见 `evals/retrieval.py`，查询与预期结果保存在 JSON 定义中。

## 真实模型评估

以下命令会调用供应商并产生费用，且只在明确提供 `--real` 或 `--live` 时执行：

```bash
uv run python -m evals.pi_model --real --scenario all --output evals/runs/local-pi.json
uv run python -m evals.pi_model --real --scenario originals --output evals/runs/local-pi-exam.json
uv run python -m evals.pi_model --real --scenario probes --output evals/runs/local-pi-boundaries.json
uv run python -m scripts.run_manual_acceptance --live --run-dir runtime/acceptance/local-manual
uv run python -m scripts.run_focused_acceptance --live --run-dir runtime/acceptance/local-focused
```

每次运行使用独立数据库和私有会话，保存实际输入、工具轨迹、正文、状态、用量和耗时。合成员工确认只验证记录流转，不代表真实设备已经恢复。

## 结果审阅

人工评价需要核对来源、建议、语言、政策适用性、因果表述、交接内容和员工隔离。预定义场景由 `scripts/run_manual_acceptance.py` 和 `scripts/run_focused_acceptance.py` 执行，汇总指标见根目录 README。提交包中的 `runtime/acceptance/` 只保留本次结果所需的精选输入、回复、工具、评分和对账证据；新生成的运行文件不会写入这些只读样本。
