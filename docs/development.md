# 开发环境

## 初始化

```bash
scripts/bootstrap.sh
```

该脚本会创建 `.venv`、安装 application 与 dev dependency，并在 `.env` 不存在时从
`.env.example` 创建本地文件。脚本可以重复运行，不会覆盖已有 `.env`。

如果默认的 `python3` 不合适，可以指定其他 executable：

```bash
PYTHON_BIN=python3.11 scripts/bootstrap.sh
```

## 启动服务

先启动本地 MongoDB：

```bash
scripts/mongo-dev.sh
```

再启动 API：

```bash
bash scripts/dev.sh
```

默认使用 `config/development.env`。浏览器 Demo 打开 <http://127.0.0.1:8000/ui>，
前端源码在 `src/smart_service_agent/web/`。传入 config path 可以切换 environment：

```bash
scripts/dev.sh config/production.env
```

## Test 与检查

```bash
scripts/test.sh
scripts/check.sh
```

`scripts/test.sh` 会把额外 argument 透传给 pytest，例如：

```bash
scripts/test.sh tests/test_health.py -q
```

## Data

`data/raw`、`data/interim` 和 `data/processed` 分别用于原始、中间和处理完成的数据。Git 只追踪
目录结构，不追踪实际数据文件。详细规则见 [`data/README.md`](../data/README.md)。

## Sandbox

运行默认的本地实验：

```bash
scripts/sandbox.sh
```

也可以把实验脚本 path 作为第一个 argument。script 会使用项目 `.venv`，默认加载
`config/development.env`，并把 `SANDBOX_OUTPUT_DIR` 指向 `sandbox/output/`。详细说明见
[`sandbox/README.md`](../sandbox/README.md)。
