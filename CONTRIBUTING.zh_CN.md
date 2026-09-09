<p align="right">
  <strong>简体中文</strong> · <a href="CONTRIBUTING.md">English</a>
</p>

# 贡献指南

感谢你愿意改进 AI Passport 模拟器！

## 基本规则

**绝不伪造设备输出。** 屏幕只显示从真实运行中的固件捕获的 framebuffer 行。
输出不可用时，界面如实显示真实状态和真实原因。渲染替代性或模拟性
"应用画面"的 PR 会被拒绝。

## 开发环境

```bash
python3 -m venv web/.venv
. web/.venv/bin/activate
pip install -r web/requirements-dev.txt
```

单元测试不依赖 QEMU 后端，但手工端到端验证需要：

```bash
tools/fetch_qemu_runtime.sh --upstream
```

## 运行检查

```bash
pytest -q web/tests worker/test_worker.py   # 全部测试
node --check web/app/static/app.js          # 前端语法
bash -n tools/*.sh                          # 脚本语法
```

CI 在 Python 3.10 和 3.12 上执行同样的检查。

## 文档语言规则

文档采用双语：英文在默认 `.md` 路径，简体中文在对应 `.zh_CN.md` 路径，
互相链接。行为变化时请同步更新两侧。

## 提交变更

1. 行为变化请先开 issue 讨论设计。
2. PR 保持聚焦，一个 PR 只做一个逻辑变更。
3. 提交前确保完整测试通过。
4. 如果改动影响 UI 或固件执行，请说明你在浏览器里手工验证了什么。

## 反馈问题

请使用 issue 模板。反馈固件显示问题时，请附上固件来源（官方目录 slug 或
自行构建）、使用的 QEMU 后端，以及日志面板的输出。
