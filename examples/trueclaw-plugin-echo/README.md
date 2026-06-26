# Echo 通道插件示例（第 12 章）

独立打包的通道插件示例，演示 `trueclaw.plugins.channel` entry point 注册。

## 安装

在仓库根目录已内置 `src/trueclaw_plugins/echo`（`PYTHONPATH=src` 即可发现）。本目录展示**拆包发布**形态：

```bash
cd examples/trueclaw-plugin-echo
pip install -e .
pip install -e ../..   # 安装 trueclaw 主包（可选，用于联调）
```

## 验收

```bash
python -c "from importlib.metadata import entry_points; print(list(entry_points(group='trueclaw.plugins.channel')))"
PYTHONPATH=../../src python3 -m trueclaw plugins list
PYTHONPATH=../../src python3 -m trueclaw plugins doctor
```

在 `trueclaw.json` 的 `channels.echo` 段启用后，网关会监听 `http://127.0.0.1:18991/echo/inbound`。
