# TaskbarHero-Harvest

TaskbarHero 收菜助手 **1.49** · 作者 **TheFallen**

通过 Windows 截图、系统 OCR 和鼠标键盘快捷键，自动整理背包、腐蚀物品、存入仓库并进行九件装备合成。界面使用 Python / Tkinter，无需 pip 依赖。

## 功能

- 分别勾选装备、材料的腐蚀品质，识别制作、装饰、雕刻及铭刻材料。
- 边扫描边执行；按材料堆叠数量添加，已确认腐蚀但数量未读清时只添加一次。
- 指定高品质物品、供奉材料自动入库；支持仓库堆叠、满页翻页和复查。
- 同品质的传奇或不朽装备每九件合成，产物收回背包。
- 按 PID 依次处理多开实例，兼容默认路径布局的 Sandboxie；切换后按一次空格。
- 可修改间隔的自动收菜、F10 停止、最新一轮记录保留及缓存清理。
- 补充物品处理规则，批量删除规则，选择性导入和导出窗口校准、补充物品。
- 页面补开、提示框位置异常恢复和多开诊断。

## 使用

需要 Windows、Python 3.10+（含 Tkinter），以及 Windows 简体中文 OCR 组件。开发与便携包验证使用 Python 3.14。程序调用系统 PowerShell / Windows Runtime OCR。

1. 下载本仓库，把整个 `harvest` 文件夹放到游戏安装目录，与 `TaskBarHero.exe` 同级。
2. 双击 `harvest/Start.cmd`。源码版使用本机 Python；已有 `harvest/runtime` 时优先使用随包运行环境。
3. 在“窗口校准”页标记背包、仓库、魔方，分别记住腐蚀和合成界面。所有待处理实例应保持相同窗口尺寸和布局。
4. 勾选腐蚀范围，点击“开始收菜”；点击“自动收菜”可按设定间隔循环，默认间隔 30 分钟。

首次启动会生成 `harvest/config.json`。仓库中的 `config.example.json` 是没有个人校准和补充规则的默认示例；无需先复制它。更新程序时保留已有 `config.json`。

按 **F10** 或点击停止按钮结束任务。完整校准步骤、各类物品处理优先级和故障诊断见 [使用说明](harvest/README.md)。

## 开发与验证

从仓库根目录运行（Windows）：

```powershell
python -X utf8 -m unittest discover -s harvest/tests -q
```

测试使用模拟输入与裁剪图片，不执行实际游戏收菜。部分用例会启动本机 Windows OCR，首次运行需要等待组件初始化。配置测试使用人工生成的校准模板，不依赖个人 `config.json`。

`harvest/check_*.py` 是识别回放与排查辅助脚本；部分旧脚本需要对应的本地 `runs` 历史记录，这些记录不在仓库内。日常验证请使用上面的 unittest 命令。

构建附带运行环境的 ZIP（需完整 Python 3.14 安装，含 Tkinter）：

```powershell
python -X utf8 harvest/build_release.py
python -X utf8 harvest/verify_release.py
```

输出位于 `dist/TaskbarHero-Harvest-1.49-TheFallen.zip`。公开构建始终打包空白默认配置，不读取本机校准；同名 ZIP 已存在时不会覆盖。验证脚本会在临时目录解压并检查文件、界面、配置管理及 OCR，不向游戏发送操作。

## 项目结构

- `harvest/app.py`：Tkinter 界面、校准及任务控制。
- `harvest/engine.py`、`streaming.py`：识别、背包扫描与动作执行。
- `harvest/rules.py`、`supplemental.py`：默认规则与补充物品规则。
- `harvest/windows_io.py`：Windows 窗口、截图、输入及 OCR。
- `harvest/assets/`：局部识别模板。
- `harvest/tests/`：单元测试与图片回归样本。

本仓库不包含游戏本体、游戏存档、个人配置、运行记录或 Python 运行环境。识别模板和测试样本中的游戏画面归游戏权利人所有；部分样本已遮去与测试无关的桌面区域。
